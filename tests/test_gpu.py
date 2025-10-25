import unittest
import torch

class TestGPU(unittest.TestCase):
    def setUp(self):
        # Setup code that runs before each test
        pass

    def test_gpu_availability(self):
        # Test if CUDA is available
        has_cuda = torch.cuda.is_available()
        self.assertTrue(has_cuda, "CUDA is not available")

    def test_gpu_memory(self):
        # Test GPU memory availability
        if torch.cuda.is_available():
            # Get GPU memory information
            gpu_memory = torch.cuda.get_device_properties(0).total_memory
            self.assertGreater(gpu_memory, 0, "GPU memory should be greater than 0")

if __name__ == '__main__':
    unittest.main()