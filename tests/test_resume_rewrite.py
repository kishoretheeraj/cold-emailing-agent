from __future__ import annotations

import subprocess
import unittest
from unittest.mock import Mock, patch

import resume_rewrite as rw


class BuildRewritePromptTests(unittest.TestCase):
    def test_prompt_contains_bullets_banned_words_and_output_instruction(self):
        bullets = [
            "Raised conversion 25% for Acme Cloud.",
            "Cut AWS costs by $40,000.",
        ]
        prompt = rw.build_rewrite_prompt(
            bullets,
            ["leverage", "delve"],
        )

        self.assertIn(bullets[0], prompt)
        self.assertIn(bullets[1], prompt)
        self.assertIn("leverage, delve", prompt)
        self.assertIn("Output ONLY a numbered list", prompt)
        self.assertIn("1. ...", prompt)

    def test_aggressive_prompt_differs(self):
        bullets = ["Raised conversion 25% for Acme Cloud."]

        standard = rw.build_rewrite_prompt(bullets, [])
        aggressive = rw.build_rewrite_prompt(
            bullets,
            [],
            aggressive=True,
        )

        self.assertNotEqual(standard, aggressive)
        self.assertIn("Restructure aggressively", aggressive)
        self.assertIn("under 30% word overlap", aggressive)


class ParseRewrittenBulletsTests(unittest.TestCase):
    def test_parse_handles_noise_and_numbering_variants(self):
        output = (
            "Warning: experimental feature enabled\n"
            "\n"
            "1) First rewritten bullet\n"
            "2. Second rewritten bullet\n"
            "unrelated trailing output\n"
        )

        self.assertEqual(
            rw.parse_rewritten_bullets(output),
            [
                "First rewritten bullet",
                "Second rewritten bullet",
            ],
        )


class RewriteBulletsTests(unittest.TestCase):
    @patch.object(rw, "_call_codex_rewriter", return_value="warning only")
    def test_zero_parsed_bullets_raises(self, call_rewriter):
        with self.assertRaises(rw.RewriteError):
            rw.rewrite_bullets(["Original bullet"], [])

        call_rewriter.assert_called_once()

    @patch.object(rw.subprocess, "run")
    def test_timeout_raises(self, run):
        run.side_effect = subprocess.TimeoutExpired(
            cmd=["codex", "exec"],
            timeout=17,
        )

        with self.assertRaisesRegex(rw.RewriteError, "timed out"):
            rw.rewrite_bullets(
                ["Original bullet"],
                [],
                timeout=17,
            )

    @patch.object(rw.subprocess, "run")
    def test_nonzero_return_code_raises(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=["codex", "exec"],
            returncode=2,
            stdout="",
            stderr="authentication failed",
        )

        with self.assertRaisesRegex(
            rw.RewriteError,
            "authentication failed",
        ):
            rw.rewrite_bullets(["Original bullet"], [])

    def test_dedupes_repeated_numbered_list(self):
        text = (
            "codex\n"
            "1. First rewrite\n"
            "2. Second rewrite\n"
            "tokens used\n"
            "2,207\n"
            "1. First rewrite\n"
            "2. Second rewrite\n"
        )
        self.assertEqual(
            rw.parse_rewritten_bullets(text),
            ["First rewrite", "Second rewrite"],
        )

    @patch.object(rw.subprocess, "run")
    def test_subprocess_invocation_uses_noninteractive_exec(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="1. Rewritten bullet\n",
            stderr="",
        )

        result = rw.rewrite_bullets(["Original bullet"], [])

        self.assertEqual(result, ["Rewritten bullet"])
        command = run.call_args.args[0]
        self.assertTrue(command[0].endswith("codex"))
        self.assertEqual(command[1:3], [
            "exec",
            "--skip-git-repo-check",
        ])
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertTrue(run.call_args.kwargs["text"])


class CodexAvailableTests(unittest.TestCase):
    def test_true_when_codex_is_on_path(self):
        with patch.object(
            rw.shutil,
            "which",
            return_value="/usr/local/bin/codex",
        ) as which:
            self.assertTrue(rw.codex_available())

        which.assert_called_once_with("codex")

    def test_false_when_codex_is_not_on_path(self):
        with patch.object(rw.shutil, "which", return_value=None) as which:
            self.assertFalse(rw.codex_available())

        which.assert_called_once_with("codex")


class SharedFourGramRatioTests(unittest.TestCase):
    def test_identical_text_has_full_overlap(self):
        bullets = ["one two three four five six"]

        self.assertEqual(
            rw.shared_4gram_ratio(bullets, bullets),
            1.0,
        )

    def test_disjoint_text_has_zero_overlap(self):
        before = ["one two three four five"]
        after = ["six seven eight nine ten"]

        self.assertEqual(
            rw.shared_4gram_ratio(before, after),
            0.0,
        )

    def test_partial_overlap_is_between_zero_and_one(self):
        before = ["one two three four five six"]
        after = ["one two three four seven eight"]

        ratio = rw.shared_4gram_ratio(before, after)

        self.assertGreater(ratio, 0.0)
        self.assertLess(ratio, 1.0)


class SwapBulletsTests(unittest.TestCase):
    def test_basic_replacement_preserves_nonbullet_lines(self):
        md = (
            "# EXPERIENCE\n"
            "Introductory text\n"
            "- Original first\n"
            "- Original second"
        )

        result = rw.swap_bullets(
            md,
            ["Original first", "Original second"],
            ["Rewritten first", "Rewritten second"],
        )

        self.assertEqual(
            result,
            (
                "# EXPERIENCE\n"
                "Introductory text\n"
                "- Rewritten first\n"
                "- Rewritten second"
            ),
        )

    def test_duplicate_bullets_are_replaced_in_order(self):
        md = (
            "# EXPERIENCE\n"
            "- Repeated bullet\n"
            "- Repeated bullet\n"
        )

        result = rw.swap_bullets(
            md,
            ["Repeated bullet", "Repeated bullet"],
            ["First replacement", "Second replacement"],
        )

        self.assertEqual(
            result,
            (
                "# EXPERIENCE\n"
                "- First replacement\n"
                "- Second replacement\n"
            ),
        )

    def test_trailing_newline_is_preserved(self):
        with_newline = "- Original\n"
        without_newline = "- Original"

        self.assertEqual(
            rw.swap_bullets(
                with_newline,
                ["Original"],
                ["Rewritten"],
            ),
            "- Rewritten\n",
        )
        self.assertEqual(
            rw.swap_bullets(
                without_newline,
                ["Original"],
                ["Rewritten"],
            ),
            "- Rewritten",
        )


if __name__ == "__main__":
    unittest.main()
