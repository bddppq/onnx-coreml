from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import unittest
import numpy as np
import numpy.testing as npt

from PIL import Image

from onnx_coreml import convert
from tests._test_utils import _onnx_create_single_node_model


class ConvertTest(unittest.TestCase):
    def setUp(self):
        self.img_arr = np.uint8(np.random.rand(224, 224, 3) * 255)
        self.img = Image.fromarray(np.uint8(self.img_arr))
        self.img_arr = np.float32(self.img_arr)
        self.onnx_model = _onnx_create_single_node_model(
            "Relu",
            [(3, 224, 224)],
            [(3, 224, 224)]
        )
        self.input_names = [i.name for i in self.onnx_model.graph.input]
        self.output_names = [o.name for o in self.onnx_model.graph.output]

    def test_convert_image_input(self):
        coreml_model = convert(
            self.onnx_model,
            image_input_names=self.input_names
        )
        spec = coreml_model.get_spec()
        for input_ in spec.description.input:
            self.assertEqual(input_.type.WhichOneof('Type'), 'imageType')

    def test_convert_image_output(self):
        coreml_model = convert(
            self.onnx_model,
            image_output_names=self.output_names
        )
        spec = coreml_model.get_spec()
        for output in spec.description.output:
            self.assertEqual(output.type.WhichOneof('Type'), 'imageType')

    def test_convert_image_input_preprocess(self):
        bias = np.array([100, 90, 80])
        coreml_model = convert(
            self.onnx_model,
            image_input_names=self.input_names,
            preprocessing_args={
                'is_bgr': True,
                'blue_bias': bias[0],
                'green_bias': bias[1],
                'red_bias': bias[2]
            }
        )
        output = coreml_model.predict(
            {
                self.input_names[0]: self.img
            }
        )[self.output_names[0]]

        expected_output = self.img_arr[:, :, ::-1].transpose((2, 0, 1))
        expected_output[0] += bias[0]
        expected_output[1] += bias[1]
        expected_output[2] += bias[2]
        npt.assert_equal(output, expected_output)

    def test_convert_image_output_bgr(self):
        coreml_model = convert(
            self.onnx_model,
            image_input_names=self.input_names,
            image_output_names=self.output_names,
            deprocessing_args={
                'is_bgr': True
            }
        )
        output = coreml_model.predict(
            {
                self.input_names[0]: self.img
            }
        )[self.output_names[0]]
        output = np.array(output)[:, :, :3].transpose(2, 0, 1)
        expected_output = self.img_arr[:, :, ::-1].transpose((2, 0, 1))
        npt.assert_equal(output, expected_output)


if __name__ == '__main__':
    unittest.main()
