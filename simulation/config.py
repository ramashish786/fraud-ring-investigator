"""Simulation configuration. Every knob that changes difficulty lives here."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AttackMix:
    mule_networks: int = 3  # layering rings fed by ATO + cash placement
    ato_victims_per_mule_net: int = 4
    synthetic_identity_rings: int = 3
    card_testing_campaigns: int = 2
    promo_abuse_rings: int = 3
    round_trip_rings: int = 2


@dataclass
class SimConfig:
    seed: int = 42
    start: datetime = datetime(2026, 9, 1)
    days: int = 60
    n_customers: int = 4000
    n_merchants: int = 300
    n_employers: int = 25  # payroll fan-out (benign trap)
    n_shopkeepers: int = 60  # UPI-style fan-in (benign trap)
    currency: str = "INR"
    reporting_threshold: float = 50_000.0  # structuring happens just below this
    attacks: AttackMix = field(default_factory=AttackMix)

    # Camouflage: how hard the attacker tries to look normal (0..1)
    camouflage: float = 0.5
