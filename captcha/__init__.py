import random
import tempfile

from typing import Tuple

import num2words

from PIL import Image
from PIL import ImageDraw
from PIL import ImageFont


def get_challenge_and_answer() -> Tuple[str, int]:
    first = random.randint(0, 30)
    second = random.randint(0, 30)
    answer = first + second
    first = num2words.num2words(first)
    second = num2words.num2words(second)
    # Open an Image
    img = Image.open(f"captcha/captcha{random.randint(1,4)}.webp")

    # Call draw Method to add 2D graphics in an image
    I1 = ImageDraw.Draw(img)

    myFont = ImageFont.truetype("FreeMono.ttf", 65)
    # Add Text to an image
    I1.text(
        (80, 80), f"What's {first}\nplus {second}?", font=myFont, fill=(255, 255, 255)
    )

    temp_file = tempfile.NamedTemporaryFile(
        prefix="rendered", suffix=".webp", delete=False
    )

    # Save the edited image
    img.save(temp_file)
    return temp_file.name, answer


if __name__ == "__main__":
    print(get_challenge_and_answer())
