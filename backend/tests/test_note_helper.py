import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "app" / "utils" / "note_helper.py"
spec = importlib.util.spec_from_file_location("note_helper", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError("note_helper module spec not found")
note_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(note_helper)


class TestNoteHelper(unittest.TestCase):
    def test_timestamp_inside_toc_link_does_not_create_nested_links(self):
        markdown = '- [阵容 *Content-[00:56]*](#阵容-content-0056)\n- [普通章节](#普通章节)'
        self.assertEqual(note_helper.replace_content_markers(markdown, 'BVtest'),
                         '- 阵容 [原片 @ 00:56](https://www.bilibili.com/video/BVtest?t=56)\n- [普通章节](#普通章节)')

    def test_timestamp_links_support_bilibili_parts_and_other_platforms(self):
        for video_id, platform, expected in [
            ('BVtest', 'bilibili', 'https://www.bilibili.com/video/BVtest?t=256'),
            ('BVtest_p2', 'bilibili', 'https://www.bilibili.com/video/BVtest?p=2&t=256'),
            ('abc', 'youtube', 'https://www.youtube.com/watch?v=abc&t=256s'),
            ('123', 'douyin', 'https://www.douyin.com/video/123'),
        ]:
            for marker in ['*Content-04:16*', 'Content-04:16', '*Content-[04:16]*']:
                with self.subTest(platform=platform, video_id=video_id, marker=marker):
                    self.assertEqual(note_helper.replace_content_markers(marker, video_id, platform),
                                     f'[原片 @ 04:16]({expected})')

    def test_prepend_source_link_adds_header_at_top(self):
        source_url = "https://www.bilibili.com/video/BV1xx411c7mD"
        markdown = "## 标题\n\n内容"

        result = note_helper.prepend_source_link(markdown, source_url)

        self.assertTrue(result.startswith(f"> 来源链接：{source_url}\n\n"))
        self.assertIn("## 标题", result)

    def test_prepend_source_link_does_not_duplicate_when_header_exists(self):
        source_url = "https://www.youtube.com/watch?v=abc123"
        markdown = f"> 来源链接：{source_url}\n\n## 标题\n\n内容"

        result = note_helper.prepend_source_link(markdown, source_url)

        self.assertEqual(result, markdown)


if __name__ == "__main__":
    unittest.main()
