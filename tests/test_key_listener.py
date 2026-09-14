"""Focused tests for KeyChord activation matching."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath('src'))

from key_listener import KeyChord, KeyCode, InputEvent  # noqa: E402


def make_ctrl_shift_space_chord():
    return KeyChord({
        frozenset({KeyCode.CTRL_LEFT, KeyCode.CTRL_RIGHT}),
        frozenset({KeyCode.SHIFT_LEFT, KeyCode.SHIFT_RIGHT}),
        KeyCode.SPACE,
    })


class KeyChordTests(unittest.TestCase):
    def test_exact_combination_activates(self):
        chord = make_ctrl_shift_space_chord()
        chord.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.SHIFT_LEFT, InputEvent.KEY_PRESS)
        self.assertTrue(chord.update(KeyCode.SPACE, InputEvent.KEY_PRESS))

    def test_unrelated_shortcut_then_fast_space_does_not_activate(self):
        """Reproduces: holding Ctrl+Shift for another app's Ctrl+Shift+K shortcut, then
        quickly releasing K and hitting Space, should not fire this chord's activation."""
        chord = make_ctrl_shift_space_chord()
        chord.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.SHIFT_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.K, InputEvent.KEY_PRESS)
        self.assertFalse(chord.is_active())
        chord.update(KeyCode.K, InputEvent.KEY_RELEASE)
        # Space arrives right after K is released, while Ctrl+Shift are still held.
        self.assertFalse(chord.update(KeyCode.SPACE, InputEvent.KEY_PRESS))

    def test_chord_rearms_after_full_release(self):
        chord = make_ctrl_shift_space_chord()
        chord.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.SHIFT_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.K, InputEvent.KEY_PRESS)
        chord.update(KeyCode.K, InputEvent.KEY_RELEASE)
        chord.update(KeyCode.SPACE, InputEvent.KEY_PRESS)
        chord.update(KeyCode.SPACE, InputEvent.KEY_RELEASE)
        chord.update(KeyCode.SHIFT_LEFT, InputEvent.KEY_RELEASE)
        chord.update(KeyCode.CTRL_LEFT, InputEvent.KEY_RELEASE)

        # Fully released; pressing the real combination now should work again.
        chord.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.SHIFT_LEFT, InputEvent.KEY_PRESS)
        self.assertTrue(chord.update(KeyCode.SPACE, InputEvent.KEY_PRESS))

    def test_foreign_key_pressed_before_chord_starts_still_disarms(self):
        chord = make_ctrl_shift_space_chord()
        chord.update(KeyCode.CTRL_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.K, InputEvent.KEY_PRESS)
        chord.update(KeyCode.SHIFT_LEFT, InputEvent.KEY_PRESS)
        chord.update(KeyCode.K, InputEvent.KEY_RELEASE)
        self.assertFalse(chord.update(KeyCode.SPACE, InputEvent.KEY_PRESS))


if __name__ == '__main__':
    unittest.main()
