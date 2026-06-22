"""Hierarchical agglomerative clustering functionality for Aspose.PDF Python SDK."""

from __future__ import annotations
from typing import Any, List, Optional


class DataPoint:
    """Represents a data point in clustering operations."""
    
    def __init__(self, name: str, value: List[float]):
        """Initialize a data point.
        
        Args:
            name: Identifier for the data point
            value: List of numeric values representing the point's coordinates
        """
        self.name = name
        self.value = value
    
    @staticmethod
    def get_centroid(cluster: "Cluster") -> "DataPoint":
        """Calculate the centroid of a cluster.
        
        Args:
            cluster: The cluster to calculate centroid for
            
        Returns:
            DataPoint representing the centroid
        """
        if cluster.count == 0:
            raise ValueError("Cannot calculate centroid of empty cluster")
        
        # Get all data points from the cluster
        points = list(cluster)
        if not points:
            raise ValueError("Cluster contains no data points")
        
        # Calculate mean of each dimension
        num_dims = len(points[0].value)
        centroid_values = [0.0] * num_dims
        
        for point in points:
            for i, val in enumerate(point.value):
                centroid_values[i] += val
        
        # Average each dimension
        for i in range(num_dims):
            centroid_values[i] /= len(points)
        
        # Use first point's name with "centroid" suffix for the centroid name
        return DataPoint("centroid", centroid_values)
    
    def __repr__(self) -> str:
        return f"DataPoint(name='{self.name}', value={self.value})"
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DataPoint):
            return False
        return self.name == other.name and self.value == other.value


class Cluster:
    """Represents a cluster of data points."""
    
    _empty_instance: Optional["Cluster"] = None
    
    def __init__(self, items: Optional[List[Any]] = None):
        """Initialize a cluster.
        
        Args:
            items: Optional list of items to initialize the cluster with
        """
        self._items: List[Any] = []
        if items is not None:
            for item in items:
                self._items.append(item)
    
    @classmethod
    def empty(cls) -> "Cluster":
        """Get the singleton empty cluster instance.
        
        Returns:
            The empty cluster instance
        """
        if cls._empty_instance is None:
            cls._empty_instance = cls()
        return cls._empty_instance
    
    @property
    def count(self) -> int:
        """Get the number of items in the cluster."""
        return len(self._items)
    
    def contains(self, item: Any) -> bool:
        """Check if the cluster contains an item.
        
        Args:
            item: The item to check for
            
        Returns:
            True if the item is in the cluster, False otherwise
        """
        return item in self._items
    
    def clone(self) -> "Cluster":
        """Create a copy of this cluster.
        
        Returns:
            A new Cluster with the same items
        """
        return Cluster(self._items.copy())
    
    def __iter__(self):
        """Iterate over items in the cluster."""
        return iter(self._items)
    
    def __len__(self) -> int:
        """Get the number of items in the cluster."""
        return self.count
    
    def __eq__(self, other: object) -> bool:
        """Check equality with another cluster.
        
        Two clusters are equal if they contain the same items.
        """
        if not isinstance(other, Cluster):
            return False
        return self._items == other._items
    
    def __repr__(self) -> str:
        return f"Cluster({self._items})"


class ClusterCollection:
    """A collection of clusters."""
    
    def __init__(self, clusters: Optional[List[Cluster]] = None):
        """Initialize a cluster collection.
        
        Args:
            clusters: Optional list of clusters to initialize with
        """
        self._clusters: List[Cluster] = []
        if clusters is not None:
            for cluster in clusters:
                self._clusters.append(cluster)
    
    def __len__(self) -> int:
        """Get the number of clusters in the collection."""
        return len(self._clusters)
