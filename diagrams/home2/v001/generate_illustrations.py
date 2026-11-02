from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "docs" / "assets" / "home2" / "v001"
BLUE = "#4051b5"
TEAL = "#0d9488"
INK = "#33416a"
PALE = "#eef1fc"
SOFT = "#dbe2fa"
WHITE = "#ffffff"


def canvas():
    image = Image.new("RGBA", (1600, 900))
    return image, ImageDraw.Draw(image)


def rounded(draw, box, fill=WHITE, outline=SOFT, radius=24, width=6):
    draw.rounded_rectangle(box, radius, fill, outline, width)


def line(draw, points, fill=BLUE, width=8):
    draw.line(points, fill=fill, width=width, joint="curve")


def dotted(draw, points, fill=BLUE):
    for start, end in zip(points, points[1:]):
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = (dx * dx + dy * dy) ** 0.5
        count = max(1, int(length / 26))
        for index in range(count):
            t = index / count
            x, y = start[0] + dx * t, start[1] + dy * t
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill)


def chart(draw, x, y, width=360, height=240, falling=False):
    rounded(draw, (x, y, x + width, y + height))
    line(draw, [(x + 28, y + 30), (x + 88, y + 30)], SOFT, 10)
    if falling:
        points = [
            (x + width * .12, y + height * .36),
            (x + width * .33, y + height * .44),
            (x + width * .48, y + height * .38),
            (x + width * .67, y + height * .65),
            (x + width * .85, y + height * .77),
        ]
        line(draw, points, BLUE, 10)
        end = points[-1]
        draw.ellipse((end[0] - 10, end[1] - 10, end[0] + 10, end[1] + 10), TEAL)
    else:
        baseline = y + height - 32
        for index, fraction in enumerate([.32, .48, .39, .67]):
            left = x + 40 + index * (width - 80) / 4
            bar_width = (width - 100) / 6
            rounded(
                draw,
                (left, baseline - height * fraction, left + bar_width, baseline),
                TEAL if index == 3 else BLUE,
                None,
                8,
            )


def storefront(draw, x, y, width=440, height=350):
    rounded(draw, (x + 20, y + 80, x + width - 20, y + height), WHITE, SOFT)
    rounded(draw, (x + 65, y + 170, x + width * .5, y + height - 40), PALE, SOFT, 8)
    rounded(draw, (x + width * .61, y + 170, x + width - 60, y + height), SOFT, None, 8)
    line(draw, [(x + width * .69, y + 250), (x + width * .69, y + 275)], BLUE, 8)
    draw.polygon(
        [(x + 20, y + 80), (x + width - 20, y + 80),
         (x + width, y + 150), (x, y + 150)],
        SOFT,
    )
    for index in range(0, 8, 2):
        left = x + width * index / 8
        right = x + width * (index + 1) / 8
        draw.polygon(
            [(left + 20 * (1 - index / 8), y + 80),
             (right + 20 * (1 - (index + 1) / 8), y + 80),
             (right, y + 150), (left, y + 150)],
            BLUE,
        )
    rounded(draw, (x + 50, y + 15, x + width - 50, y + 72), BLUE, None, 12)
    line(draw, [(x + 155, y + 44), (x + width - 155, y + 44)], WHITE, 8)
    line(draw, [(x + 12, y + height + 10), (x + width - 12, y + height + 10)], INK, 5)


def envelope(draw, x, y, width=220, height=150, reply=False):
    rounded(draw, (x, y, x + width, y + height), WHITE, BLUE, 18, 7)
    line(draw, [(x + 12, y + 16), (x + width / 2, y + height * .57),
                (x + width - 12, y + 16)], BLUE, 7)
    if reply:
        draw.ellipse((x + width - 55, y - 40, x + width + 35, y + 50), TEAL)
        line(draw, [(x + width + 5, y + 3), (x + width - 24, y + 3),
                    (x + width - 24, y + 22)], WHITE, 6)
        line(draw, [(x + width - 12, y - 10), (x + width - 26, y + 3),
                    (x + width - 12, y + 15)], WHITE, 6)


def phone(draw, x, y):
    rounded(draw, (x, y, x + 210, y + 355), BLUE, None, 36)
    rounded(draw, (x + 13, y + 16, x + 197, y + 335), WHITE, None, 27)
    rounded(draw, (x + 63, y + 24, x + 147, y + 42), BLUE, None, 9)
    for index in range(3):
        line(draw, [(x + 40, y + 205 + index * 30),
                    (x + 168 - index * 20, y + 205 + index * 30)], SOFT, 9)
    rounded(draw, (x - 82, y + 82, x + 260, y + 168), PALE, BLUE, 16, 5)
    draw.ellipse((x - 55, y + 106, x - 21, y + 140), TEAL)
    line(draw, [(x - 3, y + 113), (x + 217, y + 113)], BLUE, 8)
    line(draw, [(x - 3, y + 137), (x + 147, y + 137)], SOFT, 8)


def event(draw, x, y):
    draw.ellipse((x - 136, y - 136, x + 136, y + 136), PALE)
    draw.ellipse((x - 106, y - 106, x + 106, y + 106), BLUE)
    draw.polygon(
        [(x + 14, y - 72), (x - 53, y + 10), (x - 6, y + 10),
         (x - 20, y + 72), (x + 58, y - 13), (x + 9, y - 13)],
        WHITE,
    )


def sales():
    image, draw = canvas()
    draw.ellipse((180, 100, 1420, 860), PALE)
    storefront(draw, 300, 340, 590, 400)
    chart(draw, 780, 140, 440, 300, falling=True)
    draw.ellipse((1120, 570, 1280, 730), SOFT)
    line(draw, [(1160, 650), (1200, 610), (1240, 650)], BLUE, 10)
    return image


def recovery():
    image, draw = canvas()
    draw.ellipse((180, 100, 1420, 860), PALE)
    rounded(draw, (400, 330, 800, 710), BLUE, None, 24)
    draw.arc((500, 210, 700, 430), 180, 360, INK, 12)
    line(draw, [(500, 320), (500, 395)], INK, 12)
    line(draw, [(700, 320), (700, 395)], INK, 12)
    line(draw, [(790, 480), (840, 480), (910, 680), (1130, 680)], INK, 12)
    draw.polygon([(862, 525), (1150, 525), (1100, 640), (904, 640)], SOFT)
    line(draw, [(862, 525), (1150, 525), (1100, 640), (904, 640),
                (862, 525)], BLUE, 8)
    for x in [940, 1090]:
        draw.ellipse((x - 22, 695, x + 22, 739), BLUE)
    envelope(draw, 900, 210, 300, 190, reply=True)
    return image


def shipment():
    image, draw = canvas()
    draw.ellipse((180, 100, 1420, 860), PALE)
    dotted(draw, [(310, 725), (1180, 725), (1300, 610),
                  (1300, 320), (1110, 230)], SOFT)
    rounded(draw, (310, 400, 880, 670), BLUE, None, 20)
    draw.polygon([(890, 470), (1020, 470), (1150, 585),
                  (1150, 670), (890, 670)], TEAL)
    draw.polygon([(920, 495), (1000, 495), (1082, 574), (920, 574)], WHITE)
    for x in [460, 780, 1030]:
        draw.ellipse((x - 58, 610, x + 58, 726), INK)
        draw.ellipse((x - 27, 641, x + 27, 695), WHITE)
    draw.ellipse((990, 125, 1150, 285), TEAL)
    draw.polygon([(1007, 242), (1133, 242), (1070, 340)], TEAL)
    draw.ellipse((1040, 175, 1100, 235), WHITE)
    line(draw, [(480, 250), (730, 250)], BLUE, 10)
    draw.polygon([(730, 250), (700, 230), (700, 270)], BLUE)
    return image


def hero():
    image, draw = canvas()
    draw.ellipse((40, 100, 1550, 860), PALE)
    dotted(draw, [(510, 430), (670, 430)])
    dotted(draw, [(930, 430), (1040, 430), (1040, 290), (1105, 290)])
    dotted(draw, [(930, 430), (985, 430), (985, 635), (1040, 635)], TEAL)
    storefront(draw, 90, 380, 440, 350)
    chart(draw, 150, 115, 350, 225, falling=True)
    event(draw, 800, 430)
    phone(draw, 1190, 135)
    chart(draw, 1060, 550, 400, 240)
    return image


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, make in [
        ("event-to-action", hero),
        ("sales-alert", sales),
        ("cart-recovery", recovery),
        ("shipment-response", shipment),
    ]:
        image = make()
        image = image.resize((1200, 675), Image.Resampling.LANCZOS)
        destination = OUTPUT / f"{name}.png"
        image.save(destination, optimize=True)
        print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
