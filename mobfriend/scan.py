import zbar, numpy
import numpy
import zbar.misc
from PIL import Image

scanner = zbar.Scanner()

def scan(image_path):
    image = numpy.asarray(Image.open(image_path))
    if len(image.shape) == 3:
        image = zbar.misc.rgb2gray(image)
    results = scanner.scan(image)
    return [
        (result.type, result.data, result.quality, result.position)
        for result in results
    ]
