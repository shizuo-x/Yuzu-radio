import logging
import json
import os
import aiohttp
from typing import Dict, List, Optional
import config

logger = logging.getLogger('discord_bot.core.stations')

class StationManager:
    """Manages radio stations from PocketBase or a local JSON file."""

    def __init__(self, bot):
        self.bot = bot
        self.stations: Dict[str, Dict] = {}
        self.source = "Unknown"

    async def fetch_stations(self):
        """Fetches stations from the configured source."""
        if config.POCKETBASE_URL:
            await self._fetch_from_pocketbase()
        else:
            self._load_from_json()

    async def _fetch_from_pocketbase(self):
        """Fetches stations from a PocketBase instance."""
        self.source = "PocketBase"
        url = f"{config.POCKETBASE_URL}/api/collections/stations/records"
        params = {
            "sort": "-created",
            "perPage": 500  # Adjust if you have significantly more stations
        }
        
        try:
            # Check if http_session exists and is open
            if not self.bot.http_session or self.bot.http_session.closed:
                # If session is closed/missing, create a temporary one for this request
                # This is a fallback; ideally bot.http_session should be available
                logger.warning("Bot HTTP session unavailable. Creating temporary session for station fetch.")
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, params=params) as response:
                        await self._process_pb_response(response)
            else:
                # Use the bot's shared session
                async with self.bot.http_session.get(url, params=params) as response:
                    await self._process_pb_response(response)

        except Exception as e:
            logger.error(f"Failed to fetch stations from PocketBase: {e}. Falling back to JSON.")
            self._load_from_json()

    async def _process_pb_response(self, response):
        """Helper to process PocketBase API response."""
        if response.status == 200:
            data = await response.json()
            items = data.get('items', [])
            self.stations = {}
            for item in items:
                # Map PocketBase fields to our internal structure
                # Assuming PocketBase schema has: name, stream_url, image_url, country, tags, is_featured
                name = item.get('name')
                if name and item.get('stream_url'):
                    self.stations[name] = {
                        "url": item.get('stream_url'),
                        "desc": f"{item.get('country', 'Unknown')} - {', '.join(item.get('tags', []))}",
                        "image_url": item.get('image_url', ''),
                        "is_featured": item.get('is_featured', False)
                    }
            logger.info(f"Loaded {len(self.stations)} stations from PocketBase.")
        else:
            logger.error(f"PocketBase returned status {response.status}. Falling back to JSON.")
            self._load_from_json()

    def _load_from_json(self):
        """Loads stations from the local JSON file."""
        self.source = "Local JSON"
        try:
            if os.path.exists(config.STATIONS_FILE):
                with open(config.STATIONS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stations = {}
                    for item in data:
                        name = item.get('name')
                        if name and item.get('stream_url'):
                            self.stations[name] = {
                                "url": item.get('stream_url'),
                                "desc": f"{item.get('country', 'Unknown')} - {', '.join(item.get('tags', []))}",
                                "image_url": item.get('image_url', ''),
                                "is_featured": item.get('is_featured', False)
                            }
                logger.info(f"Loaded {len(self.stations)} stations from local JSON.")
            else:
                logger.error(f"Stations file not found: {config.STATIONS_FILE}")
                self.stations = {}
        except Exception as e:
            logger.error(f"Error loading stations from JSON: {e}")
            self.stations = {}

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