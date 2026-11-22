"""Headless Pymunk replay of logged grid trajectories."""

import gzip
import json
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
import pymunk
from PIL import Image, ImageDraw


def replay_video(trajectory, output, fps=12, substeps=3, max_steps=150):
    if min(fps, substeps, max_steps) < 1:
        raise ValueError("Replay rates and limits must be positive")
    with gzip.open(trajectory, "rt") as stream:
        data = json.load(stream)
    grid = np.asarray(data["grid"])
    scale, top = 18, 54
    width, height = grid.shape[1] * scale, grid.shape[0] * scale + top
    width += width % 2
    height += height % 2
    background = Image.new("RGB", (width, height), "#f6f8fb")
    draw = ImageDraw.Draw(background)
    space = pymunk.Space()
    for y, x in np.argwhere(grid == 0):
        x, y = int(x), int(y)
        body = pymunk.Body(body_type=pymunk.Body.STATIC)
        body.position = x + 0.5, y + 0.5
        shape = pymunk.Poly.create_box(body, (1, 1))
        space.add(body, shape)
        draw.rectangle(
            (x * scale, y * scale + top, (x + 1) * scale, (y + 1) * scale + top),
            fill="#384b5a",
        )
    for y, x in data["goals"]:
        draw.rectangle(
            (x * scale + 6, y * scale + top + 6, x * scale + 12, y * scale + top + 12),
            outline="#9aa8b4",
        )
    bodies = []
    for _ in data["aware"]:
        body = pymunk.Body(body_type=pymunk.Body.KINEMATIC)
        shape = pymunk.Circle(body, 0.28)
        shape.sensor = True
        space.add(body, shape)
        bodies.append(body)
    frames = data["frames"][: max_steps + 1]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered = 0
    with imageio.get_writer(
        output, fps=fps, codec="libx264", macro_block_size=2, ffmpeg_log_level="error"
    ) as writer:
        for t, (previous, current) in enumerate(zip(frames, frames[1:])):
            active = []
            for i, (before, after) in enumerate(zip(previous, current)):
                bodies[i].velocity = 0, 0
                if before is not None and after is not None:
                    active.append(i)
                    bodies[i].position = before[1] + 0.5, before[0] + 0.5
                    bodies[i].velocity = after[1] - before[1], after[0] - before[0]
            for sub in range(substeps):
                space.step(1 / substeps)
                image = background.copy()
                draw = ImageDraw.Draw(image)
                moment = t + (sub + 1) / substeps
                arrived = sum(a is not None and a <= moment for a in data["arrivals"])
                draw.text(
                    (8, 6),
                    f"Pymunk trajectory replay | step {moment:.1f} | arrived {arrived}/{len(bodies)}",
                    fill="#172f43",
                )
                draw.text(
                    (8, 23),
                    "Orange: aware   Blue: unaware   Squares: goals",
                    fill="#172f43",
                )
                draw.text(
                    (8, 38),
                    "Kinematic visualization; not a physical safety validation",
                    fill="#526370",
                )
                for i in active:
                    px, py = bodies[i].position
                    x, y, radius = px * scale, py * scale + top, 0.28 * scale
                    draw.ellipse(
                        (x - radius, y - radius, x + radius, y + radius),
                        fill="#e88725" if data["aware"][i] else "#2478b5",
                    )
                writer.append_data(np.asarray(image))
                if rendered == 0:
                    image.save(output.with_suffix(".png"))
                rendered += 1
    return rendered
