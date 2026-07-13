"""
    Simple local file-based annotations class (no InfluxDB required)
    Author: Philipp Sommer
"""
import os
import json
from datetime import datetime


class LocalAnnotations:
    """This class is a simple wrapper for writing annotations to a JSON file
    """

    def __init__(self, output_file=None, **kwargs):
        """Initialize the LocalAnnotations class
        
        Args:
            output_file: Path to the output JSON file. If None, creates a timestamped file.
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
            self.output_file = f"annotations_{timestamp}.json"
        else:
            self.output_file = output_file

    def createEvent(self, timestamp, title, description=None, tags=None):
        """ Create a new annotation event
        
        Args:
            timestamp: Unix timestamp (seconds)
            title: Title of the event
            description: Optional description
            tags: Optional dictionary of tags
        """
        
        event = {
            "time": int(timestamp * 1e9),  # Convert to nanoseconds for compatibility
            "timestamp_readable": datetime.fromtimestamp(timestamp).isoformat(),
            "measurement": "events",
            "tags": tags or {},
            "fields": {"title": title, "description": description}
        }

        self._write_to_file(event)

    def createRegion(self, timestamp_start, timestamp_end, title, description=None, tags=None):
        """ Create a new annotation event region
        
        Args:
            timestamp_start: Unix timestamp for region start (seconds)
            timestamp_end: Unix timestamp for region end (seconds)
            title: Title of the event
            description: Optional description
            tags: Optional dictionary of tags
        """
        
        event = {
            "time": int(timestamp_start * 1e9),  # Convert to nanoseconds for compatibility
            "timestamp_readable": datetime.fromtimestamp(timestamp_start).isoformat(),
            "timeEnd": int(timestamp_end * 1e9),
            "timeEnd_readable": datetime.fromtimestamp(timestamp_end).isoformat(),
            "measurement": "events",
            "tags": tags or {},
            "fields": {"title": title, "description": description}
        }

        self._write_to_file(event)

    def _write_to_file(self, event):
        """Write an event to the JSON file (one JSON object per line)"""
        try:
            with open(self.output_file, "a") as f:
                f.write(json.dumps(event) + "\n")
        except Exception as e:
            print(f"Error writing annotation to {self.output_file}: {e}")
