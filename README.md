# fbc

fbc keeps track of a record shop's stock and sales, and keeps them in step
with Discogs and eBay. It's a Flask-AppBuilder app on MariaDB.

- **Inventory**: Units, the Purchase Lots they came in and the Storage Boxes
  they're kept in.
- **Discogs**: the Discogs collection, synced in, with Add Unit for releases
  that have none yet.
- **Sales**: eBay listings and orders, synced in, with eBay Drafts to list a
  Unit and Ship This to turn an order into a Sales Receipt.
- **Supplies**: the packing supplies sales use up, Purchase Orders to restock
  them, and Reconcile Supplies to line the Supply List up with what's on
  hand.
- **Expenses**: business costs that aren't Purchase Lots or Purchase Orders,
  like an eBay Store subscription, equipment or mileage.

## Documentation

- [Using fbc](docs/using.md): what each part of the app does, and how to
  use it.
- [Developing fbc](docs/development.md): running the app on this machine,
  the tests and migrations, the Docker setup, a copy of live data and fake
  eBay and Discogs records.
- [Running fbc in production](docs/production.md): `config.py`, settings
  from the environment, and starting, stopping and updating the app on the
  live server.

## Quick start

To work on the app, with its own MariaDB in Docker, in dev mode, so it can't
reach the live eBay and Discogs accounts:

```bash
pipenv install --dev
pre-commit install
dev-scripts/compose-up.sh
```

Then open http://127.0.0.1:5555. [Developing fbc](docs/development.md) has
the steps before the first start.

On the live server, from the repo root, to build the image and start the
app on port 20000:

```bash
docker/live-up.sh
```

[Running fbc in production](docs/production.md) covers `config.py` and
`docker/.env`, which it needs first.
