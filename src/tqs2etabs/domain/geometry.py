"""Geometria plana basica, independente de TQS e ETABS."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float

    def distance_to(self, other: "Point") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def __iter__(self):
        yield self.x
        yield self.y


Polygon = tuple[Point, ...]


def open_polygon(points: Iterable[Point], tol: float = 1e-9) -> Polygon:
    """Remove o vertice final repetido (poligono 'fechado' explicitamente)."""
    pts = tuple(points)
    if len(pts) >= 2 and pts[0].distance_to(pts[-1]) <= tol:
        pts = pts[:-1]
    return pts


def polygon_area_signed(poly: Sequence[Point]) -> float:
    """Area pelo shoelace; positiva para anti-horario."""
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        s += a.x * b.y - b.x * a.y
    return 0.5 * s


def polygon_area(poly: Sequence[Point]) -> float:
    return abs(polygon_area_signed(poly))


def polygon_centroid(poly: Sequence[Point]) -> Point:
    a = polygon_area_signed(poly)
    if abs(a) < 1e-12:
        n = len(poly)
        return Point(sum(p.x for p in poly) / n, sum(p.y for p in poly) / n)
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        cross = p.x * q.y - q.x * p.y
        cx += (p.x + q.x) * cross
        cy += (p.y + q.y) * cross
    return Point(cx / (6 * a), cy / (6 * a))


def polygon_bbox(poly: Sequence[Point]) -> tuple[Point, Point]:
    xs = [p.x for p in poly]
    ys = [p.y for p in poly]
    return Point(min(xs), min(ys)), Point(max(xs), max(ys))


def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    """Ray casting; pontos exatamente sobre a borda nao sao garantidos."""
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a.y > p.y) != (b.y > p.y):
            xi = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y)
            if xi > p.x:
                inside = not inside
    return inside


def distance_point_to_segment(p: Point, a: Point, b: Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    ll = dx * dx + dy * dy
    if ll == 0.0:
        return p.distance_to(a)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / ll))
    return p.distance_to(Point(a.x + t * dx, a.y + t * dy))


def distance_point_to_polygon_boundary(p: Point, poly: Sequence[Point]) -> float:
    n = len(poly)
    return min(distance_point_to_segment(p, poly[i], poly[(i + 1) % n]) for i in range(n))


def rotate(vx: float, vy: float, angle_deg: float) -> tuple[float, float]:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    return c * vx - s * vy, s * vx + c * vy


def rectangle_from_local(origin: Point, length: float, width: float, angle_deg: float) -> Polygon:
    """Retangulo com eixo local u (comprimento) rotacionado de angle_deg em relacao ao X global.

    Vertices em ordem: origem, +u, +u+v, +v.
    """
    corners = ((0.0, 0.0), (length, 0.0), (length, width), (0.0, width))
    out = []
    for u, v in corners:
        gx, gy = rotate(u, v, angle_deg)
        out.append(Point(origin.x + gx, origin.y + gy))
    return tuple(out)


def polyline_length(points: Sequence[Point]) -> float:
    return sum(points[i].distance_to(points[i + 1]) for i in range(len(points) - 1))


def scale_point(p: Point, factor: float) -> Point:
    return Point(p.x * factor, p.y * factor)


def scale_polygon(poly: Sequence[Point], factor: float) -> Polygon:
    return tuple(scale_point(p, factor) for p in poly)
