import logging
import json
import os
import re
import time
import asyncio
import aiohttp
from typing import Dict, List, Optional
import config

logger = logging.getLogger('discord_bot.core.stations')


def _parse_tags(raw) -> List[str]:
    """Normalizes a tags field (JSON string or list) to a list of strings."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = []
    if isinstance(raw, list):
        return [str(t) for t in raw if t]
    return []


def _make_station_id(name: str) -> str:
    """Builds a stable slug id from a station name (for JSON-only stations)."""
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


class StationManager:
    """Manages radio stations from PocketBase or a local JSON file."""

    def __init__(self, bot):
        self.bot = bot
        self.stations: Dict[str, Dict] = {}
        self.source = "Unknown"
        self._fetch_lock = asyncio.Lock()
        self.last_fetch = 0.0  # time.monotonic() of the last fetch attempt
        self.last_fetch_ok = False

    async def fetch_stations(self):
        """Fetches stations from the configured source."""
        async with self._fetch_lock:
            if config.POCKETBASE_URL:
                await self._fetch_from_pocketbase()
            else:
                self._load_from_json()
            self.last_fetch = time.monotonic()

    async def ensure_fresh(self, max_age_seconds: float = 300):
        """Fetches stations only if the cached data is older than max_age_seconds."""
        if time.monotonic() - self.last_fetch > max_age_seconds:
            await self.fetch_stations()

    async def _fetch_from_pocketbase(self):
        """Fetches stations from a PocketBase instance (paginated)."""
        url = f"{config.POCKETBASE_URL}/api/collections/stations/records"
        max_pages = 30  # Hard safety cap

        temp_session = None
        try:
            # Check if http_session exists and is open
            if not self.bot.http_session or self.bot.http_session.closed:
                # If session is closed/missing, create a temporary one for this request
                # This is a fallback; ideally bot.http_session should be available
                logger.warning("Bot HTTP session unavailable. Creating temporary session for station fetch.")
                temp_session = aiohttp.ClientSession()
            session = temp_session or self.bot.http_session

            new_stations: Dict[str, Dict] = {}
            page = 1
            total_pages = 1
            while page <= total_pages and page <= max_pages:
                params = {"page": page, "perPage": 100, "sort": "-created"}
                async with session.get(url, params=params) as response:
                    total_pages = await self._process_pb_response(response, new_stations)
                page += 1

            self.stations = new_stations
            self._rebuild_sorted()
            self.source = "PocketBase"
            self.last_fetch_ok = True
            logger.info(f"Loaded {len(self.stations)} stations from PocketBase.")
        except Exception as e:
            self.last_fetch_ok = False
            if not self.stations:
                # First load with nothing cached: fall back to the local JSON file
                logger.warning(f"Failed to fetch stations from PocketBase: {e}. Falling back to JSON.")
                self._load_from_json()
            else:
                # Keep the previously loaded stations; never wipe on failure
                logger.warning(f"Failed to fetch stations from PocketBase: {e}. Keeping {len(self.stations)} cached stations.")
        finally:
            if temp_session is not None:
                await temp_session.close()

    async def _process_pb_response(self, response, stations: Dict[str, Dict]) -> int:
        """Helper to process one PocketBase API response page into `stations`.

        Returns the total number of pages reported by PocketBase.
        """
        if response.status != 200:
            raise ValueError(f"PocketBase returned status {response.status}")
        data = await response.json()
        items = data.get('items', [])
        for item in items:
            # Map PocketBase fields to our internal structure
            # Assuming PocketBase schema has: name, stream_url, image_url, country, tags, is_featured
            name = item.get('name')
            if name and item.get('stream_url'):
                tags = _parse_tags(item.get('tags'))
                stations[name] = {
                    "name": name,
                    "url": item.get('stream_url'),
                    "country": item.get('country') or '',
                    "tags": tags,
                    "desc": f"{item.get('country', 'Unknown')} - {', '.join(tags)}",
                    "image_url": item.get('image_url', ''),
                    "is_featured": bool(item.get('is_featured', False)),
                    "station_id": item.get('id') or _make_station_id(name),
                }
        try:
            return int(data.get('totalPages', 1) or 1)
        except (ValueError, TypeError):
            return 1

    def _load_from_json(self):
        """Loads stations from the local JSON file."""
        self.source = "Local JSON"
        self.last_fetch_ok = True
        try:
            if os.path.exists(config.STATIONS_FILE):
                with open(config.STATIONS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stations = {}
                    for item in data:
                        name = item.get('name')
                        if name and item.get('stream_url'):
                            tags = _parse_tags(item.get('tags'))
                            self.stations[name] = {
                                "name": name,
                                "url": item.get('stream_url'),
                                "country": item.get('country') or '',
                                "tags": tags,
                                "desc": f"{item.get('country', 'Unknown')} - {', '.join(tags)}",
                                "image_url": item.get('image_url', ''),
                                "is_featured": bool(item.get('is_featured', False)),
                                "station_id": _make_station_id(name),
                            }
                self._rebuild_sorted()
                logger.info(f"Loaded {len(self.stations)} stations from local JSON.")
            else:
                logger.error(f"Stations file not found: {config.STATIONS_FILE}")
                self.stations = {}
        except Exception as e:
            logger.error(f"Error loading stations from JSON: {e}")
            self.stations = {}

    def _rebuild_sorted(self):
        """Rebuilds self.stations sorted case-insensitively by name (stable),
        so numeric index lookups are stable across sessions."""
        self.stations = dict(sorted(self.stations.items(), key=lambda kv: kv[0].casefold()))

    def get_station(self, name: str) -> Optional[Dict]:
        """Returns station data by name (case-insensitive)."""
        # Direct lookup
        if name in self.stations:
            return self.stations[name]
        
        # Case-insensitive lookup
        name_lower = name.lower()
        for key, data in self.stations.items():
            if key.lower() == name_lower:
                return data
        return None

    def get_all_stations(self) -> Dict[str, Dict]:
        """Returns all loaded stations."""
        return self.stations

    def get_all_ordered(self) -> List[tuple[str, Dict]]:
        """Returns all stations as (name, data) tuples in alphabetical order
        (for UI pagination and autocomplete)."""
        return list(self.stations.items())

    def get_by_id(self, station_id: str) -> Optional[tuple[str, Dict]]:
        """Returns (name, station_data) for a station_id, or None."""
        for name, data in self.stations.items():
            if data.get('station_id') == station_id:
                return name, data
        return None

    def search_stations(self, query: Optional[str] = None, limit: int = 8,
                        country: Optional[str] = None, tag: Optional[str] = None) -> List[tuple[str, Dict]]:
        """
        Searches stations by tokenized scoring over name, country, and tags.
        Applies country/tag filters before scoring. Returns the top `limit`
        results as a list of (name, station_data) tuples sorted by score desc
        then name asc. With no query, returns the first `limit` stations in
        alphabetical order (browse mode).
        """
        # Apply country/tag filters BEFORE scoring
        candidates = []
        for name, data in self.stations.items():
            if country:
                station_country = (data.get('country') or '').casefold()
                if country.casefold() not in station_country:
                    continue
            if tag:
                tag_filter = tag.casefold()
                station_tags = [t.casefold() for t in data.get('tags', [])]
                if not any(tag_filter == t or tag_filter in t for t in station_tags):
                    continue
            candidates.append((name, data))

        # Browse mode: no query, alphabetical first `limit`
        if query is None or not query.strip():
            return candidates[:limit]

        query_cf = query.casefold()
        tokens = query_cf.split()
        scored = []
        for name, data in candidates:
            name_cf = name.casefold()
            tags_cf = [t.casefold() for t in data.get('tags', [])]
            country_cf = (data.get('country') or '').casefold()
            score = 0
            if name_cf == query_cf:
                score += 100
            elif name_cf.startswith(query_cf):
                score += 60
            elif tokens and all(t in name_cf for t in tokens):
                score += 40
            else:
                score += min(20 * sum(1 for t in tokens if t in name_cf), 30)
            for t in tokens:
                if t in tags_cf:
                    score += 15
                elif any(t in tg for tg in tags_cf):
                    score += 8
                if t == country_cf:
                    score += 15
            if score > 0:
                scored.append((score, name, data))

        scored.sort(key=lambda s: (-s[0], s[1].casefold()))
        return [(name, data) for _, name, data in scored[:limit]]

    def fuzzy_find_station(self, query: str) -> tuple[Optional[str], Optional[Dict]]:
        """
        Finds a station by index (1-based), exact name, or partial name.
        Returns (station_name, station_data) or (None, None).
        """
        keys = list(self.stations.keys())

        # 1. Try by Index
        if query.isdigit():
            index = int(query) - 1
            if 0 <= index < len(keys):
                name = keys[index]
                return name, self.stations[name]

        # 2. Try Exact Match (Case-Insensitive)
        query_lower = query.lower()
        for name in keys:
            if name.lower() == query_lower:
                return name, self.stations[name]

        # 3. Try Partial Match (Case-Insensitive)
        # Prefer matches that start with the query
        for name in keys:
            if name.lower().startswith(query_lower):
                 return name, self.stations[name]
        
        # Fallback to any substring match
        for name in keys:
            if query_lower in name.lower():
                return name, self.stations[name]

        return None, None