#!/usr/bin/env python3
"""Generate browser and home-screen icons from a transparent 512x512 PNG.

Requires Python 3.10+ and ImageMagick 7 (the magick command on PATH).
Python uses only its standard library. All image processing uses ImageMagick.

Usage:
    ./tools/gen-favicon.py logo.png public --name "My website"
    ./tools/gen-favicon.py logo.png public/icons --url-prefix /icons --background '#ffffff'
    ./tools/gen-favicon.py logo.png public --small-input logo-simple.png

The optional --small-input is a transparent square PNG at least 32x32 pixels.
It replaces the source for sizes up to 32x32, including frames inside the ICO.

The output directory defaults to ./favicons. Existing generated filenames are
overwritten; unrelated files are left alone. Publish its contents at --url-prefix
(the site root by default), then paste favicon-snippet.html into your HTML head.
Merge the generated manifest icons into an existing manifest if you have one.
Pass --no-manifest to skip writing site.webmanifest and omit its HTML link.
An existing manifest in the output directory is left untouched.

Browser PNGs and the ICO retain transparency. Apple and manifest icons get an
opaque background. A separate maskable icon keeps artwork inside its safe circle.
Mitchell filtering balances edge sharpness and ringing for transparent artwork.
Each size is rendered directly from the original, with linear-light resizing.
A raster input cannot supply a true vector favicon or Safari pinned-tab mask.

References:
    https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Manifest/Reference/icons
    https://web.dev/learn/pwa/web-app-manifest
    https://web.dev/articles/icons-and-browser-colors
"""

import argparse
import html
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


FAVICON_SIZES = (16, 32, 48, 96)
APP_SIZES = (192, 512)


def color(value: str) -> str:
    """Limit colors to opaque RGB so image, HTML and manifest colors agree."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise argparse.ArgumentTypeError("expected an opaque color in #RRGGBB format")
    return value.lower()


def url_prefix(value: str) -> str:
    """Use root-relative URLs so the snippet works on nested HTML pages too."""
    parsed = urlsplit(value)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or parsed.scheme
        or parsed.netloc
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() for character in value)
    ):
        raise argparse.ArgumentTypeError("expected a root-relative URL path, e.g. /icons")
    return value.rstrip("/")


def magick(*arguments: str) -> str:
    """Pass arguments without a shell and surface ImageMagick errors cleanly."""
    result = subprocess.run(["magick", *arguments], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "ImageMagick failed")
    return result.stdout


def load_source(path: Path, *, small: bool = False) -> str:
    """Require 512x512 artwork, or square artwork of at least 32px for small icons."""
    requirement = "a square PNG at least 32x32 pixels" if small else "a 512x512 PNG"
    # Confirm the actual file type rather than trusting its extension or coder.
    with path.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"{path}: input must be {requirement}")
        # APNG's animation control precedes IDAT. The PNG decoder can otherwise
        # silently accept only the default image of an animated input.
        while header := stream.read(8):
            if len(header) != 8:
                raise ValueError("input has a truncated PNG chunk")
            length = int.from_bytes(header[:4], "big")
            if header[4:] == b"acTL":
                raise ValueError("input must be a still PNG, not an animated PNG")
            if header[4:] == b"IDAT":
                break
            stream.seek(length + 4, 1)

    source = f"PNG:{path.resolve()}"
    # Explicit alpha activation makes opaque images report alpha=1 consistently.
    info = magick(
        source, "-alpha", "set", "-format",
        "%w %h %[fx:minima.a] %[fx:maxima.a]", "info:",
    ).split()
    width, height, minimum, maximum = map(float, info)
    valid_size = width == height and width >= 32 if small else (width, height) == (512, 512)
    if not valid_size:
        raise ValueError(f"{path}: input must be {requirement}")
    if minimum == 1:
        raise ValueError("input must have transparent pixels")
    if maximum == 0:
        raise ValueError("input must contain visible artwork")
    return source


def write_icon(source: str, output: Path, size: int, background: str | None = None, maskable: bool = False) -> None:
    """Resize in linear light with alpha intact before optional compositing."""
    arguments = [source]
    if maskable:
        # A transparent border makes trimming work even with opaque corners.
        arguments += ["-bordercolor", "none", "-border", "1", "-trim", "+repage"]
        # An inscribed square keeps even corner pixels inside the safe circle.
        target = math.floor(size * 0.8 / math.sqrt(2))
    else:
        target = size
    arguments += [
        "-colorspace", "RGB", "-filter", "Mitchell", "-resize", f"{target}x{target}",
        "-clamp",
    ]
    if background is not None:
        # Interpret the supplied web color as sRGB, then composite in linear RGB.
        arguments += ["(", "-size", f"{size}x{size}", f"xc:{background}", "-colorspace", "RGB", ")"]
        arguments += ["+swap", "-gravity", "center", "-compose", "Over", "-composite", "-alpha", "off"]
    arguments += ["-colorspace", "sRGB", "-depth", "8", f"PNG:{output.resolve()}"]
    magick(*arguments)


def write_metadata(output: Path, prefix: str, name: str, background: str, theme: str, *, manifest_enabled: bool = True) -> None:
    """Generate the HTML snippet and optionally its linked manifest."""
    if manifest_enabled:
        # Resolve image URLs relative to the manifest's deployed directory.
        icons = [
            {
                "src": f"android-chrome-{size}x{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any",
            }
            for size in APP_SIZES
        ]
        icons.append({
            "src": "maskable-icon-512x512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        })
        # Leave launch URL, scope and display mode to the site rather than guessing
        # application behavior from the asset directory's URL.
        manifest = {
            "name": name,
            "icons": icons,
            "theme_color": theme,
            "background_color": background,
        }
        (output / "site.webmanifest").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    escaped_prefix = html.escape(prefix, quote=True)
    lines = [
        f'<link rel="icon" type="image/x-icon" sizes="16x16 32x32 48x48" href="{escaped_prefix}/favicon.ico">',
    ]
    for size in FAVICON_SIZES:
        lines.append(
            f'<link rel="icon" type="image/png" sizes="{size}x{size}" href="{escaped_prefix}/favicon-{size}x{size}.png">'
        )
    lines.append(f'<link rel="apple-touch-icon" sizes="180x180" href="{escaped_prefix}/apple-touch-icon.png">')
    if manifest_enabled:
        lines.append(f'<link rel="manifest" href="{escaped_prefix}/site.webmanifest">')
    lines.append(f'<meta name="theme-color" content="{theme}">')
    (output / "favicon-snippet.html").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """Expose deployment paths separately from local output paths."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("input", type=Path, help="transparent 512x512 PNG")
    parser.add_argument("output", type=Path, nargs="?", default=Path("favicons"), help="output directory (default: ./favicons)")
    parser.add_argument("--small-input", type=Path, help="alternate transparent square PNG, at least 32x32, for sizes <= 32x32")
    parser.add_argument("--url-prefix", type=url_prefix, default="/", help="public URL directory (default: /)")
    parser.add_argument("--name", default="Website", help="website name in the manifest")
    parser.add_argument("--background", type=color, default="#ffffff", help="Apple/app icon background (default: #ffffff)")
    parser.add_argument("--theme-color", type=color, help="browser theme color (default: background color)")
    parser.add_argument("--no-manifest", action="store_true", help="skip manifest generation and its HTML link; leave existing manifests untouched")
    args = parser.parse_args()

    try:
        if shutil.which("magick") is None:
            raise ValueError("ImageMagick 7 is required; install it and put magick on PATH")
        source = load_source(args.input)
        small_source = load_source(args.small_input, small=True) if args.small_input else source
        filenames = [f"favicon-{size}x{size}.png" for size in FAVICON_SIZES]
        filenames += [f"android-chrome-{size}x{size}.png" for size in APP_SIZES]
        filenames += ["favicon.ico", "apple-touch-icon.png", "maskable-icon-512x512.png", "favicon-snippet.html"]
        if not args.no_manifest:
            filenames.append("site.webmanifest")
        # Protect both originals if either already uses a generated filename.
        inputs = [args.input] + ([args.small_input] if args.small_input else [])
        if any(path.resolve() == (args.output / name).resolve() for path in inputs for name in filenames):
            raise ValueError("input path collides with a generated file; choose another output directory")

        args.output.mkdir(parents=True, exist_ok=True)
        for size in FAVICON_SIZES:
            # The ICO is assembled from these PNGs, so it inherits this choice.
            icon_source = small_source if size <= 32 else source
            write_icon(icon_source, args.output / f"favicon-{size}x{size}.png", size)
        # Package the already rendered sizes so ICO frames use identical pixels.
        magick(
            *(f"PNG:{(args.output / f'favicon-{size}x{size}.png').resolve()}" for size in (16, 32, 48)),
            f"ICO:{(args.output / 'favicon.ico').resolve()}",
        )

        for size, filename in [
            (180, "apple-touch-icon.png"),
            *((size, f"android-chrome-{size}x{size}.png") for size in APP_SIZES),
        ]:
            write_icon(source, args.output / filename, size, args.background)
        write_icon(source, args.output / "maskable-icon-512x512.png", 512, args.background, maskable=True)
        write_metadata(
            args.output, args.url_prefix, args.name, args.background,
            args.theme_color or args.background, manifest_enabled=not args.no_manifest,
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")

    print(f"Generated {len(filenames)} files in {args.output}")
    print(f"Paste {args.output / 'favicon-snippet.html'} into your HTML <head>.")


if __name__ == "__main__":
    main()
