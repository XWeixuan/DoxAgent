def published_events(self, ticker: str, version: int | None = None) -> list[CanonicalEvent]:
        normalized = self._ticker(ticker)
        selected_version = self.published_version(normalized) if version is None else version
        if selected_version < 0 or selected_version > self.published_version(normalized):
            raise UnknownLibraryObjectError(
                f"library version {selected_version} is not Published for {normalized}"
            )
        if selected_version == 0:
            return []
        with self._read() as connection:
            self._require_native_version(connection,normalized,selected_version)
            rows = connection.execute(
                """
                SELECT e.event_no
                FROM canonical_events e
                JOIN canonical_event_states s
                  ON s.ticker=e.ticker AND s.event_no=e.event_no
                 AND s.library_version=(
                    SELECT MAX(s2.library_version) FROM canonical_event_states s2
                    WHERE s2.ticker=e.ticker AND s2.event_no=e.event_no
                      AND s2.library_version<=?
                 )
                WHERE e.ticker=? AND e.created_version<=? AND s.status='ACTIVE'
                ORDER BY e.event_no
                """,
                (selected_version, normalized, selected_version),
            ).fetchall()
            return [
                self._event_from_connection(
                    connection,
                    normalized,
                    int(row["event_no"]),
                    selected_version,
                )
                for row in rows
            ]
