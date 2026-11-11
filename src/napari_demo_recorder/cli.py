"""Command line interface: ``napari-demo-recorder render|play|record|validate|schema|list-elements``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _isolate_napari_settings() -> None:
    """Give napari a throw-away settings file so renders look the same on every machine and
    never modify the user's real settings."""
    if "NAPARI_CONFIG" not in os.environ:
        import tempfile

        d = tempfile.mkdtemp(prefix="napari-demo-recorder-")
        os.environ["NAPARI_CONFIG"] = str(Path(d) / "settings.yaml")


def _need_display() -> None:
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            print(
                "No display found. Run under a virtual display, e.g.\n"
                '  xvfb-run -a -s "-screen 0 1920x1080x24" napari-demo-recorder render ...',
                file=sys.stderr,
            )


def cmd_validate(args) -> int:
    from .core.format import DemoError, load_demo

    ok = True
    for f in args.files:
        try:
            spec = load_demo(f)
            print(f"OK  {f}  ({len(spec.setup)} setup + {len(spec.steps)} steps, app: {spec.app_name})")
        except DemoError as err:
            ok = False
            print(f"ERR {err}", file=sys.stderr)
    return 0 if ok else 1


def cmd_schema(args) -> int:
    from .core.adapter import registry_for
    from .core.format import full_schema

    schema = full_schema(registry_for(args.app))
    text = json.dumps(schema, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def cmd_steps(args) -> int:
    from .core.adapter import registry_for

    reg = registry_for(args.app)
    for name, st in sorted(reg.items()):
        sh = f"  (shorthand: {st.shorthand})" if st.shorthand else ""
        print(f"{name:18s} {st.doc}{sh}")
        if args.verbose:
            for k, v in st.params.items():
                print(f"{'':20s}{k}: {v.get('description', v.get('type', v.get('enum', '')))}")
    return 0


def cmd_render(args) -> int:
    from .core.format import DemoError, load_demo
    from .core.player import Player
    from .core.readme import publish

    _isolate_napari_settings()
    _need_display()
    status = 0
    for f in args.files:
        try:
            spec = load_demo(f)
            if args.speed:
                spec.data["speed"] = args.speed
            player = Player(spec, mode=args.mode, allow_python=not args.no_python, log=None if args.quiet else print)
            out = args.output if len(args.files) == 1 else None
            result = player.run(out)
            if result is not None:
                publish(spec, result, markdown=args.markdown, update_readme=not args.no_readme)
                if args.check and not result.fits:
                    status = 1
        except DemoError as err:
            print(f"ERROR {err}", file=sys.stderr)
            status = 1
            if args.fail_fast:
                break
    _shutdown()
    return status


def cmd_play(args) -> int:
    args.mode = "play"
    args.output = None
    args.markdown = False
    args.no_readme = True
    args.check = False
    args.fail_fast = True
    args.speed = getattr(args, "speed", None)
    return cmd_render(args)


def cmd_record(args) -> int:
    from .core.recorder import record_app

    _isolate_napari_settings()
    _need_display()
    return record_app(args.app, args.output, setup=args.setup)


def cmd_list_elements(args) -> int:
    from .core.adapter import adapter_class

    _isolate_napari_settings()
    from .core.format import DemoSpec

    spec = DemoSpec(data={"demo": 1, "app": args.app, "steps": []})
    adapter = adapter_class(spec.app_name)(spec.app, Path.cwd())
    window = adapter.start({})
    window.resize(1280, 800)
    window.show()
    from qtpy.QtWidgets import QApplication

    for _ in range(20):
        QApplication.processEvents()
    from .core.elements import list_elements

    for line in list_elements(window, adapter, parent=args.parent):
        print(line)
    adapter.close()
    _shutdown()
    return 0


def _shutdown() -> None:
    try:
        from qtpy.QtCore import QThreadPool

        QThreadPool.globalInstance().waitForDone(3000)
    except Exception:
        pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="napari-demo-recorder", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("render", help="render demo file(s) to GIF / MP4")
    r.add_argument("files", nargs="+")
    r.add_argument("-o", "--output", help="output path (single file only)")
    r.add_argument("--no-python", action="store_true", help="refuse 'python' steps (untrusted demos)")
    r.add_argument("--markdown", action="store_true", help="print a Markdown snippet for each output")
    r.add_argument("--no-readme", action="store_true", help="do not update README markers")
    r.add_argument("--check", action="store_true", help="exit 1 if a GIF exceeds max_size")
    r.add_argument("--fail-fast", action="store_true")
    r.add_argument("--speed", type=float, help="override the demo speed")
    r.add_argument("-q", "--quiet", action="store_true")
    r.set_defaults(func=cmd_render, mode="render")

    pl = sub.add_parser("play", help="play a demo live (preview, nothing is written)")
    pl.add_argument("files", nargs=1)
    pl.add_argument("--no-python", action="store_true")
    pl.add_argument("--speed", type=float)
    pl.add_argument("-q", "--quiet", action="store_true")
    pl.set_defaults(func=cmd_play)

    rec = sub.add_parser("record", help="use an app by hand and save what you do as a demo file")
    rec.add_argument("-o", "--output", required=True, help="demo file to write (*.demo.yaml)")
    rec.add_argument("--app", default="napari", help="'napari' or 'module:factory' for any Qt app")
    rec.add_argument("--setup", help="existing demo file whose setup runs first and is copied")
    rec.set_defaults(func=cmd_record)

    v = sub.add_parser("validate", help="check demo files without running them")
    v.add_argument("files", nargs="+")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("schema", help="print the JSON schema (for editors and LLMs)")
    s.add_argument("--app", default="napari")
    s.add_argument("-o", "--output")
    s.set_defaults(func=cmd_schema)

    st = sub.add_parser("steps", help="list available step types")
    st.add_argument("--app", default="napari")
    st.add_argument("-v", "--verbose", action="store_true")
    st.set_defaults(func=cmd_steps)

    le = sub.add_parser("list-elements", help="list the names you can target in an app")
    le.add_argument("--app", default="napari")
    le.add_argument("--parent")
    le.set_defaults(func=cmd_list_elements)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code = args.func(args)
    sys.stdout.flush()
    sys.stderr.flush()
    if args.command in ("render", "play", "list-elements", "record"):
        # Qt / vispy teardown at interpreter exit can abort; we are done anyway.
        os._exit(code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
