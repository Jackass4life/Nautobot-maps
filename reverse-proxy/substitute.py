"""Fill in ${NAME} in an nginx file (#768): ``substitute.py infile outfile --only A,B``.

Run by the Dockerfile at build time with the build args.  Only the names given with
--only are filled in, from the environment (and --env, a .env file, or ./.env): the
others stay ${...}, for nginx's entrypoint to fill in when the container starts (the
secrets, never in the image).  A name given but not set, or a value that would change
the configuration's syntax, stops it.
"""

import argparse
import os
import re

import dotenv

# A value goes into a directive as it is: no ; { } quotes, $ or whitespace.
SAFE_VALUE = re.compile(r"[A-Za-z0-9._:/@-]+")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("infile", type=str, help="input file")
    parser.add_argument("outfile", type=str, help="output file")
    parser.add_argument("--env", type=str, help="environment file", default=None)
    parser.add_argument("--only", type=str, required=True, help="the names to fill in, A,B,C")

    args = parser.parse_args()

    if args.env is not None:
        dotenv.load_dotenv(args.env)
    else:
        dotenv.load_dotenv()

    with open(args.infile) as f:
        text = f.read()

    names = [name.strip() for name in args.only.split(",") if name.strip()]
    for name in names:
        value = os.environ.get(name, "")
        if not SAFE_VALUE.fullmatch(value):
            raise SystemExit(f"{args.infile}: {name} must be set, without spaces, quotes, ; {{ }} or $ (got {value!r})")
        text = text.replace(f"${{{name}}}", value)

    with open(args.outfile, "w") as f:
        f.write(text)
