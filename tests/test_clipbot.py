import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from clipbot import analysis
from clipbot.chat import ChatLog, parse_irc_privmsg, parse_kick_event
from clipbot.clipper import DISCORD_MAX_BYTES, chunks_for, clip_window, cut_clip, make_upload_file
from clipbot.config import Config, Streamer, load_streamers
from clipbot.platforms import parse_kick_channels, parse_twitch_streams, parse_youtube_live_page
from clipbot.recorder import Chunk, read_segment_list
from clipbot.session import deletable
from clipbot.store import Clip, ClipStore

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class PlatformParsing(unittest.TestCase):
    def test_twitch_only_live(self):
        s = Streamer("twitch", "Kaicenat")
        out = parse_twitch_streams(
            {"data": [{"user_login": "kaicenat", "type": "live", "title": "GTA RP"}]}, {"kaicenat": s}
        )
        self.assertEqual(out[s.key].url, "https://www.twitch.tv/kaicenat")
        self.assertEqual(parse_twitch_streams({"data": []}, {"kaicenat": s}), {})

    def test_kick(self):
        s = Streamer("kick", "adinross")
        live = {"data": [{"slug": "adinross", "stream_title": "gta", "stream": {"is_live": True}}]}
        off = {"data": [{"slug": "adinross", "stream": {"is_live": False}}]}
        self.assertIn(s.key, parse_kick_channels(live, {"adinross": s}))
        self.assertEqual(parse_kick_channels(off, {"adinross": s}), {})

    def test_youtube_live_vs_upcoming(self):
        s = Streamer("youtube", "@someone")
        canon = '<link rel="canonical" href="https://www.youtube.com/watch?v=abcdefghijk">'
        info = parse_youtube_live_page(canon + '"isLiveNow":true <meta name="title" content="GTA live">', s)
        self.assertEqual(info.video_id, "abcdefghijk")
        self.assertEqual(info.title, "GTA live")
        self.assertIsNone(parse_youtube_live_page(canon + '"isLiveNow":false', s))


class ChatParsing(unittest.TestCase):
    def test_irc(self):
        self.assertEqual(parse_irc_privmsg(":a!a@a.tmi.twitch.tv PRIVMSG #x :KEKW no way"), "KEKW no way")
        self.assertIsNone(parse_irc_privmsg(":tmi.twitch.tv 001 justinfan1 :Welcome"))

    def test_kick(self):
        raw = json.dumps({"event": "App\\Events\\ChatMessageEvent", "data": json.dumps({"content": "LMAO"})})
        self.assertEqual(parse_kick_event(raw), "LMAO")
        self.assertIsNone(parse_kick_event(json.dumps({"event": "pusher:pong", "data": {}})))

    def test_chatlog_window(self):
        log = ChatLog()
        log.add("a", 10.0)
        log.add("b", 20.0)
        self.assertEqual(log.between(15, 25), [(20.0, "b")])


class Scoring(unittest.TestCase):
    EMOTES = ["KEKW", "LUL", "😂", "W"]

    def test_hype_detection(self):
        self.assertTrue(analysis.is_hype("KEKW KEKW", self.EMOTES))
        self.assertTrue(analysis.is_hype("dog 😂😂", self.EMOTES))
        self.assertTrue(analysis.is_hype("w", self.EMOTES))
        self.assertFalse(analysis.is_hype("what was that", self.EMOTES))

    def test_chat_spike_found(self):
        # 2 meddelanden/s i 10 min, men 30 KEKW/s under 5 s vid 300 s
        chat = [(t + 0.5, "hello") for t in range(600) for _ in range(2)]
        chat += [(300 + i / 30, "KEKW") for i in range(150)]
        scores = analysis.score_chunk(600, chat, [], self.EMOTES)
        moments = analysis.pick_moments(scores, threshold=2.5, max_moments=3, min_gap=60)
        self.assertEqual(len(moments), 1)
        self.assertTrue(300 <= moments[0].t <= 306)
        self.assertIn("emotes", moments[0].reason)

    def test_quiet_stream_gives_nothing(self):
        chat = [(t + 0.5, "hello") for t in range(600)]
        loud = [-30.0 + (t % 3) for t in range(600)]
        scores = analysis.score_chunk(600, chat, loud, self.EMOTES)
        self.assertEqual(analysis.pick_moments(scores, 2.5, 3, 60), [])

    def test_min_gap_and_max(self):
        scores = [(0.0, "")] * 600
        for t in (100, 110, 300, 500):
            scores[t] = (5.0, "x")
        picked = analysis.pick_moments(scores, 2.5, max_moments=2, min_gap=60)
        self.assertEqual(len(picked), 2)
        self.assertGreaterEqual(abs(picked[0].t - picked[1].t), 60)

    def test_parse_ebur128(self):
        err = ("[Parsed_ebur128_0 @ 0x1] t: 0.1  TARGET:-23 LUFS    M: -inf S:-inf I: -70 LUFS\n"
               "[Parsed_ebur128_0 @ 0x1] t: 1.2  TARGET:-23 LUFS    M: -20.5 S:-30 I: -25 LUFS\n")
        self.assertEqual(analysis.parse_ebur128(err), [-70.0, -20.5])


class ClipMath(unittest.TestCase):
    def test_window_has_buildup(self):
        self.assertEqual(clip_window(100, 40, 15), (60, 115))
        self.assertEqual(clip_window(10, 40, 15), (0.0, 25))
        self.assertEqual(clip_window(100, 40, 15, rec_end=105), (60, 105))

    def test_chunks_for_spans_boundary(self):
        chunks = [Chunk(Path("a"), 0, 600), Chunk(Path("b"), 600, 1200), Chunk(Path("c"), 1200, 1800)]
        self.assertEqual([c.path.name for c in chunks_for(chunks, 580, 640)], ["a", "b"])

    def test_deletable(self):
        chunks = [Chunk(Path("a"), 0, 600), Chunk(Path("b"), 600, 1200)]
        self.assertEqual([c.path.name for c in deletable(chunks, 1160)], ["a"])
        self.assertEqual(deletable(chunks, 500), [])

    def test_segment_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "chunks.csv"
            p.write_text("chunk_0000.ts,0.000000,600.033333\nchunk_0001.ts,600.033333,1200.0\n")
            chunks = read_segment_list(p, Path(d))
            self.assertEqual(len(chunks), 2)
            self.assertAlmostEqual(chunks[1].start, 600.033333)


class ConfigAndStore(unittest.TestCase):
    def test_streamers_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text(json.dumps([{"platform": "Twitch", "channel": "abc", "name": "ABC"}]))
            s = load_streamers(p)[0]
            self.assertEqual((s.platform, s.key, s.display), ("twitch", "twitch:abc", "ABC"))
            p.write_text(json.dumps([{"platform": "myspace", "channel": "x"}]))
            with self.assertRaises(ValueError):
                load_streamers(p)

    def test_dry_run_default(self):
        self.assertTrue(Config.from_env().dry_run)

    def test_publish_mode(self):
        self.assertTrue(Config().manual_publishing)  # ingen Post for Me-nyckel -> gratisläget
        self.assertFalse(Config(postforme_api_key="k", postforme_account_ids=["a"]).manual_publishing)
        self.assertTrue(Config(postforme_api_key="k", postforme_account_ids=["a"], publish_mode="manual").manual_publishing)

    def test_store_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            store = ClipStore(Path(d) / "clips.json")
            clip = store.add(Clip("A", "twitch", "t", "a.mp4", "p.mp4", 3.1, "ljud"))
            store.update(clip.id, status="rejected")
            again = ClipStore(Path(d) / "clips.json")
            self.assertEqual(again.clips[clip.id].status, "rejected")
            self.assertEqual(again.pending(), [])


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg saknas")
class EndToEndVideo(unittest.TestCase):
    """Gör en 'stream' på 3 min med en ljudexplosion vid 130 s, delar den i 1-minutersbitar
    som Recorder gör, och kontrollerar att toppen hittas och klippet blir 9:16 med uppbyggnad."""

    def test_finds_loud_moment_and_cuts_vertical_clip(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "stream.mp4"
            audio = "if(between(t,130,134),0.9,0.02)*sin(2*PI*440*t)"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=180",
                "-f", "lavfi", "-i", f"aevalsrc='{audio}':s=44100:d=180",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "30", "-c:a", "aac", "-shortest", str(src),
            ], check=True)
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c", "copy", "-f", "segment",
                "-segment_time", "60", "-reset_timestamps", "1", "-segment_list", str(d / "chunks.csv"),
                "-segment_list_type", "csv", str(d / "chunk_%04d.ts"),
            ], check=True)
            chunks = read_segment_list(d / "chunks.csv", d)
            self.assertEqual(len(chunks), 3)

            third = chunks[2]  # 120–180 s
            loud = analysis.audio_loudness(str(third.path))
            moments = analysis.pick_moments(analysis.score_chunk(61, [], loud, []), 2.5, 3, 60)
            self.assertEqual(len(moments), 1)
            peak = third.start + moments[0].t
            self.assertTrue(129 <= peak <= 135, peak)

            start, end = clip_window(peak, 40, 15)
            out = cut_clip(chunks, start, end, d / "clip.mp4", vertical=True)  # går över bitgränsen vid 120 s
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-show_entries", "format=duration", "-of", "json", str(out)],
                capture_output=True, text=True, check=True,
            )
            info = json.loads(probe.stdout)
            self.assertEqual((info["streams"][0]["width"], info["streams"][0]["height"]), (1080, 1920))
            self.assertAlmostEqual(float(info["format"]["duration"]), end - start, delta=1.5)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg saknas")
class UploadFile(unittest.TestCase):
    def test_long_detailed_clip_fits_discord(self):
        """Ett 60 s 1080x1920-klipp med mycket rörelse ska ändå bli en 720x1280-fil under Discords gräns."""
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "clip.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "mandelbrot=size=1080x1920:rate=30",
                "-f", "lavfi", "-i", "anoisesrc=d=60", "-t", "60", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "18", "-c:a", "aac", str(src),
            ], check=True)
            out = make_upload_file(src, Path(d) / "up.mp4")
            self.assertLessEqual(out.stat().st_size, DISCORD_MAX_BYTES)
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True,
            )
            self.assertEqual(probe.stdout.strip(), "720,1280")


if __name__ == "__main__":
    unittest.main()
