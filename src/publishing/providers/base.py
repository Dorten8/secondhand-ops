"""
base.py — Abstract provider interface for marketplace listing operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class BaseListingProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier: 'dba' or 'facebook'."""
        pass

    @abstractmethod
    def validate_ad_spec(self, spec: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Validate ad fields prior to publishing. Returns (is_valid, error_reason)."""
        pass

    @abstractmethod
    def sync_active_ads(self, **kwargs) -> List[Dict[str, Any]]:
        """Fetch active ads from the marketplace account."""
        pass
