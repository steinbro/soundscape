#!/usr/bin/env python3
"""Charm for the PostGIS-enabled PostgreSQL server used by the Soundscape data pipeline.

Translation of the `postgis` service in svcs/data/docker-compose.yml.
"""

import logging
import secrets

import ops

logger = logging.getLogger(__name__)

CONTAINER_NAME = "postgis"
PGDATA = "/var/lib/postgresql/data"
# A dot-file inside PGDATA: initdb explicitly permits directories containing
# only dot-files (or lost+found) to still be treated as "empty", so storing
# our generated password here doesn't interfere with first-time init, and it
# stays durable on the same volume as the data it authenticates - unlike
# ops.StoredState, which is not guaranteed to survive pod recreation.
PASSWORD_FILE = f"{PGDATA}/.juju-postgres-password"
RELATION_NAME = "database"
PORT = 5432


class PostgisCharm(ops.CharmBase):
    """Runs postgis/postgis and publishes connection details to related apps."""

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        framework.observe(self.on[CONTAINER_NAME].pebble_ready, self._reconcile)
        framework.observe(self.on.config_changed, self._reconcile)
        framework.observe(self.on.upgrade_charm, self._reconcile)
        framework.observe(self.on[RELATION_NAME].relation_joined, self._reconcile)
        framework.observe(self.on.leader_elected, self._reconcile)

    def _password(self, container: ops.Container) -> str:
        configured = self.config.get("postgres-password")
        if configured:
            return str(configured)
        # Generated once and persisted on the pgdata volume so it survives
        # charm upgrades/pod restarts and never drifts from the password
        # postgres actually initialized the "postgres" role with (postgres
        # only honors POSTGRES_PASSWORD on first init of an empty PGDATA).
        if container.exists(PASSWORD_FILE):
            return container.pull(PASSWORD_FILE).read().strip()
        password = secrets.token_urlsafe(18)
        container.push(PASSWORD_FILE, password, make_dirs=True)
        return password

    def _reconcile(self, event: ops.EventBase) -> None:
        container = self.unit.get_container(CONTAINER_NAME)
        if not container.can_connect():
            self.unit.status = ops.WaitingStatus("waiting for postgis container")
            return

        password = self._password(container)
        container.add_layer(CONTAINER_NAME, self._pebble_layer(password), combine=True)
        container.replan()
        self.unit.set_ports(PORT)

        self._publish_connection_details(password)

        service = container.get_service(CONTAINER_NAME)
        self.unit.status = ops.ActiveStatus() if service.is_running() else ops.WaitingStatus(
            "starting postgis"
        )

    def _pebble_layer(self, password: str) -> ops.pebble.LayerDict:
        return {
            "summary": "postgis layer",
            "services": {
                CONTAINER_NAME: {
                    "override": "replace",
                    "summary": "postgis/postgis server",
                    "command": "docker-entrypoint.sh postgres",
                    "startup": "enabled",
                    "environment": {
                        "POSTGRES_PASSWORD": password,
                        "PGDATA": PGDATA,
                        "PGUSER": "postgres",
                    },
                }
            },
            "checks": {
                "ready": {
                    "override": "replace",
                    "level": "ready",
                    "exec": {"command": "pg_isready -U postgres"},
                    "period": "10s",
                    "timeout": "5s",
                    "threshold": 5,
                }
            },
        }

    def _publish_connection_details(self, password: str) -> None:
        if not self.unit.is_leader():
            return
        relation_data = {
            "host": self.app.name,
            "port": "5432",
            "user": "postgres",
            "password": password,
            "database": str(self.config["database-name"]),
        }
        for relation in self.model.relations[RELATION_NAME]:
            relation.data[self.app].update(relation_data)


if __name__ == "__main__":  # pragma: nocover
    ops.main(PostgisCharm)
