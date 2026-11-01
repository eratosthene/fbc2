"""
Add fake Discogs collection items to a dev database, in place of a sync
from Discogs.

  releases N  N releases in the collection, each with its own artist, a
              genre and style, and --instances copies (default 1). No Units
              are made for them, so they show in Releases w/out Units with an
              Add Unit link for each instance.

They go in a "Fake Releases" folder. --size picks the record size. Remove
everything fake with clear_fake.py.

Run from the repo root, on this machine or in docker-compose.dev.yml's app:
  pipenv run python dev-scripts/fake_discogs.py releases 5
  docker compose -f docker-compose.dev.yml exec app python dev-scripts/fake_discogs.py releases 5
"""

import argparse

import _fake


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("kind", choices=["releases"])
    parser.add_argument("count", type=int)
    parser.add_argument(
        "--size",
        choices=list(_fake.SIZES) + ["mix"],
        default="mix",
        help='record size of the releases (default: a mix, mostly 12")',
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=1,
        help="copies of each release in the collection (default 1)",
    )
    args = parser.parse_args()
    _fake.setup()
    releases = [
        _fake.fake_release(args.size, args.instances) for _ in range(args.count)
    ]
    _fake.db.session.commit()
    for release in releases:
        print(
            "Added "
            + str(release.release_id)
            + ": "
            + repr(release)
            + ", instances "
            + ", ".join(str(i.instance_id) for i in release.discogs_instances)
        )


if __name__ == "__main__":
    main()
