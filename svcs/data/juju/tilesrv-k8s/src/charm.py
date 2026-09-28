#!/usr/bin/env python3
"""Charm for the Soundscape GeoJSON tile server.

Translation of the `tilesrv-blue`/`tilesrv-green` services in
svcs/data/docker-compose.yml (blue/green rollout is not represented here).
"""

import logging
from typing import Mapping, Optional

import ops

logger = logging.getLogger(__name__)

CONTAINER_NAME = "tilesrv"
RELATION_NAME = "database"
PORT = 8080


class TilesrvCharm(ops.CharmBase):
    """Runs gentiles.py against a related PostGIS server and exposes it on PORT."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)
        framework.observe(self.on[CONTAINER_NAME].pebble_ready, self._reconcile)
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
            self.unit.status = ops.WaitingStatus("waiting for tilesrv container")
            return

        dsn = (
            f"host={db['host']} port={db['port']} "
            f"dbname={db['database']} user={db['user']} password={db['password']}"
        )
        container.add_layer(CONTAINER_NAME, self._pebble_layer(dsn), combine=True)
        container.replan()
        self.unit.set_ports(PORT)
        self.unit.status = ops.ActiveStatus()

    def _pebble_layer(self, dsn: str) -> ops.pebble.LayerDict:
        # Mirrors the ENTRYPOINT in svcs/data/Dockerfile.tilesrv.
        return {
            "summary": "tilesrv layer",
            "services": {
                CONTAINER_NAME: {
                    "override": "replace",
                    "summary": "GeoJSON tile server",
                    "command": f'python3 /tilesrv/gentiles.py --verbose --dsn "{dsn}" --server {PORT}',
                    "startup": "enabled",
                }
            },
            "checks": {
                "alive": {
                    "override": "replace",
                    "level": "alive",
                    "period": "10s",
                    "http": {"url": f"http://localhost:{PORT}/probe/alive"},
                }
            },
        }


if __name__ == "__main__":  # pragma: nocover
    ops.main(TilesrvCharm)
