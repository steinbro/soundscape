#!/usr/bin/env python3
"""Charm for the Soundscape OSM ingestion service.

Translation of the `ingest` service in svcs/data/docker-compose.yml.
"""

import logging
from typing import Mapping, Optional

import ops

logger = logging.getLogger(__name__)

CONTAINER_NAME = "ingest"
RELATION_NAME = "database"

TILES_DIR = "/tiles"
INGEST_DIR = "/ingest"
MAPPING_DIR = "/mapping"
EXTRADATA_DIR = "/non_osm_data"


class IngestCharm(ops.CharmBase):
    """Runs the imposm3-based OSM ingestion loop against a related PostGIS server."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on[CONTAINER_NAME].pebble_ready, self._reconcile)
        framework.observe(self.on.config_changed, self._reconcile)
        framework.observe(self.on.upgrade_charm, self._reconcile)
        framework.observe(self.on[RELATION_NAME].relation_changed, self._reconcile)
        framework.observe(self.on[RELATION_NAME].relation_broken, self._reconcile)

    def _database_info(self) -> Optional[Mapping[str, str]]:
        relation = self.model.get_relation(RELATION_NAME)
        if relation is None or relation.app is None:
            return None
        data = relation.data[relation.app]
        return data if data.get("host") else None

    def _reconcile(self, event: ops.EventBase) -> None:
        db = self._database_info()
        if db is None:
            self.unit.status = ops.BlockedStatus("waiting for database relation to postgis-k8s")
            return

        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            self.unit.status = ops.WaitingStatus("waiting for ingest container")
            return

        container.add_layer(CONTAINER_NAME, self._pebble_layer(db), combine=True)
        container.replan()
        self.unit.status = ops.ActiveStatus()

    def _pebble_layer(self, db: Mapping[str, str]) -> ops.pebble.LayerDict:
        # Mirrors the CMD in svcs/data/Dockerfile.ingest.
        command = (
            "/bin/sh -c '"
            f"rm -f {TILES_DIR}/*.pbf && "
            f"rm -rf {TILES_DIR}/imposm_cache {TILES_DIR}/imposm_expired {TILES_DIR}/imposm_diff && "
            # ingest.py always looks for this directory (matching the optional
            # ./non_osm_data bind mount in docker-compose.yml); create it if no
            # storage/mount has been attached so import_non_osm_data() finds an
            # empty (rather than missing) directory.
            f"mkdir -p {EXTRADATA_DIR} && "
            f"python3 {INGEST_DIR}/ingest.py "
            f"--imposm {INGEST_DIR}/imposm3/imposm "
            f"--mapping {MAPPING_DIR}/mapping.yml "
            "--where $GEN_REGIONS "
            f"--extracts {INGEST_DIR}/extracts.json "
            f"--cachedir {TILES_DIR}/imposm_cache "
            f"--diffdir {TILES_DIR}/imposm_diff "
            f"--pbfdir {TILES_DIR} "
            f"--expiredir {TILES_DIR}/imposm_expired "
            "--verbose "
            "--updatemodel importloop "
            "--delay $LOOP_TIME "
            "--telemetry "
            f"--extradatadir {EXTRADATA_DIR} "
            "$INGEST_FLAGS'"
        )
        return {
            "summary": "ingest layer",
            "services": {
                CONTAINER_NAME: {
                    "override": "replace",
                    "summary": "OSM ingestion loop",
                    "command": command,
                    "startup": "enabled",
                    "environment": {
                        "GEN_REGIONS": str(self.config["gen-regions"]),
                        "LOOP_TIME": str(self.config["loop-time"]),
                        "INGEST_FLAGS": str(self.config["ingest-flags"]),
                        "NAMESPACE": self.model.name,
                        "POSTGIS_HOST": db["host"],
                        "POSTGIS_PORT": db["port"],
                        "POSTGIS_USER": db["user"],
                        "POSTGIS_PASSWORD": db["password"],
                        "POSTGIS_DBNAME": db["database"],
                    },
                }
            },
        }


if __name__ == "__main__":  # pragma: nocover
    ops.main(IngestCharm)
