"""
Commands that change live eBay listings. They are attached to `flask` by
fbc.commands.register.
"""

import click
from flask.cli import with_appcontext


@click.command("set-listing-skus")
@click.option(
    "--apply", is_flag=True, help="Change the listings; without it, only show."
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="Also replace a SKU typed on eBay that isn't fbc-<unit id>.",
)
@click.option("--item", help="Only this eBay item ID.")
@click.option("--limit", type=int, help="Change at most this many listings.")
@click.option(
    "--csv",
    "csv_path",
    default="skipped-listing-skus.csv",
    show_default=True,
    help="Where to write the listings it skipped, and why. - prints the CSV"
    " to stdout, with the rest of the output on stderr.",
)
@with_appcontext
def set_listing_skus(apply, overwrite, item, limit, csv_path):
    """Set fbc-<unit id> as the SKU on live listings that lack it."""
    from fbc.ebay import skus

    # With the CSV on stdout, say everything else on stderr so it stays clean.
    to_stdout = csv_path == "-"

    def say(text):
        click.echo(text, err=to_stdout)

    try:
        result = skus.set_skus(
            apply=apply, overwrite=overwrite, item=item, limit=limit, echo=say
        )
    except skus.EbayError as e:
        raise click.ClickException(str(e))
    counts = result["counts"]
    say("")
    say(("Set " if apply else "Would set ") + str(counts[skus.SET]) + ".")
    say("Already right: " + str(counts[skus.RIGHT]) + ".")
    for reason in skus.SKIP_REASONS:
        say("Skipped, " + reason + ": " + str(counts[reason]) + ".")
    text = skus.skipped_csv(result["skipped"])
    if to_stdout:
        click.echo(text, nl=False)
    else:
        with open(csv_path, "w", newline="") as f:
            f.write(text)
        say("Wrote " + str(len(result["skipped"])) + " skipped to " + csv_path + ".")
    if not apply:
        say("This was a dry run. Add --apply to change the listings.")


@click.command("update-listing-descriptions")
@click.option(
    "--apply", is_flag=True, help="Change the listings; without it, only show."
)
@click.option("--item", help="Only this eBay item ID.")
@click.option("--limit", type=int, help="Change at most this many listings.")
@with_appcontext
def update_listing_descriptions(apply, item, limit):
    """Give active listings the description the Description Template makes."""
    from fbc.ebay import redescribe

    try:
        counts = redescribe.update_all(
            apply=apply, item=item, limit=limit, echo=click.echo
        )["counts"]
    except ValueError as e:
        raise click.ClickException(str(e))
    click.echo("")
    click.echo(("Set " if apply else "Would set ") + str(counts[redescribe.SET]) + ".")
    click.echo("Already right: " + str(counts[redescribe.RIGHT]) + ".")
    for reason in redescribe.SKIP_REASONS:
        click.echo("Skipped, " + reason + ": " + str(counts[reason]) + ".")
    if not apply:
        click.echo("This was a dry run. Add --apply to change the listings.")
