import sys
sys.path.insert(0, '..')
from fbc.models.inventory import Unit
from fbc.models.discogs import DiscogsRelease
from fbc import db
from sqlalchemy import select
import pprint

units = db.session.query(Unit).all()
for unit in units:
    if unit.discogs_release:
        master_id = unit.discogs_release.master_id
        release_id = unit.discogs_release.release_id
        if master_id > 0:
            unit.discogs_master = str(master_id)
            print(str(unit.id) + " master set to " + str(unit.discogs_master))
        else:
            unit.discogs_master = 'r' + str(release_id)
            print(str(unit.id) + " master set to " + str(unit.discogs_master))
        db.session.commit()

