import unittest
import numpy as np
from app.video.face_mask import FaceMask


class FaceMaskEdges(unittest.TestCase):
    def make_mask(self):
        mask = FaceMask()
        mask.current_mask = np.full((8, 12, 4), 255, np.uint8)
        mask.mask_size = 31
        mask.mask_x, mask.mask_y = 0, 0
        return mask

    def test_off_canvas_mask_does_not_crash_or_change_frame(self):
        mask = self.make_mask()
        mask.mask_x = -100
        frame = np.zeros((30, 40, 3), np.uint8)
        result = mask.apply(frame.copy())
        np.testing.assert_array_equal(result, frame)

    def test_odd_mask_size_blends_and_grayscale_is_supported(self):
        mask = self.make_mask()
        mask.current_mask = np.full((8, 12), 255, np.uint8)
        result = mask.apply(np.zeros((30, 40, 3), np.uint8))
        self.assertGreater(result.sum(), 0)

    def test_manual_drag_is_released(self):
        mask = self.make_mask()
        self.assertTrue(mask.handle_click(10, 10))
        mask.handle_drag(15, 20)
        self.assertEqual((mask.mask_x, mask.mask_y), (15, 20))
        mask.handle_release()
        self.assertFalse(mask.is_dragging)


if __name__ == '__main__':
    unittest.main()
