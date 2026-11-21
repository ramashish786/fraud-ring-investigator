"""The synthetic bank: entities, identifiers, and benign customer behaviour.

Benign behaviour deliberately includes *traps* — legitimate activity that looks
like fraud to naive rules (shared household devices, carrier-grade NAT IPs,
payroll fan-out, shopkeeper fan-in, friends splitting bills, new-phone logins).
Every trap is recorded so false positives can be explained, not just counted.
"""

from __future__ import annotations

import itertools
import random
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

from .config import SimConfig

FIRST = [
    "Aarav",
    "Vivaan",
    "Aditya",
    "Ishaan",
    "Rohan",
    "Kabir",
    "Arjun",
    "Sai",
    "Ananya",
    "Diya",
    "Isha",
    "Meera",
    "Priya",
    "Riya",
    "Sara",
    "Kavya",
    "Neha",
    "Pooja",
    "Rahul",
    "Amit",
    "Vikram",
    "Sneha",
    "Tanvi",
    "Nikhil",
    "Zoya",
    "Farhan",
    "Imran",
    "Joseph",
    "Maria",
    "Gurpreet",
]
LAST = [
    "Sharma",
    "Verma",
    "Iyer",
    "Nair",
    "Patel",
    "Shah",
    "Khan",
    "Reddy",
    "Das",
    "Gupta",
    "Mehta",
    "Joshi",
    "Singh",
    "Kulkarni",
    "Menon",
    "Pillai",
    "Bose",
    "Chopra",
    "Rao",
    "Sahu",
]
CITIES = ["Mumbai", "Pune", "Bengaluru", "Delhi", "Hyderabad", "Chennai", "Kolkata", "Ahmedabad"]
EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com"]
MERCHANT_CATS = {  # category: (lognormal mean, sigma, risk)
    "grocery": (6.2, 0.6, "low"),
    "fuel": (7.0, 0.4, "low"),
    "restaurant": (6.5, 0.6, "low"),
    "pharmacy": (6.0, 0.7, "low"),
    "utilities": (7.3, 0.5, "low"),
    "travel": (8.6, 0.8, "medium"),
    "fashion": (7.5, 0.7, "medium"),
    "electronics": (9.3, 0.8, "high"),
    "gift_cards": (8.0, 0.6, "high"),
    "jewellery": (10.0, 0.7, "high"),
    "digital_goods": (4.5, 0.9, "medium"),
    "donations": (4.0, 0.8, "medium"),
}


class World:
    def __init__(self, cfg: SimConfig):
        self.cfg = cfg
        self.rng = np.random.default_rng(cfg.seed)
        self.r = random.Random(cfg.seed)
        self._ctr = defaultdict(itertools.count)
        self.end = cfg.start + timedelta(days=cfg.days)

        # entity tables
        self.customers, self.accounts, self.devices = {}, {}, {}
        self.ips, self.merchants, self.cards = {}, {}, {}
        # event tables
        self.logins, self.txns, self.account_events, self.referrals = [], [], [], []
        # ground truth
        self.rings: list[dict] = []
        self.traps: dict[str, set] = defaultdict(set)
        # per-customer state used by behaviour + attacks
        self.cust_devices = defaultdict(list)
        self.cust_ips = defaultdict(list)
        self.friends = defaultdict(set)
        self.extras: dict = {}  # phase-2 outputs (beneficiaries, cases, alerts)
        self.used: set[str] = set()  # customers already assigned a fraud role
        self._acct_by_cust = defaultdict(list)
        self._card_by_acct = {}

    # ------------------------------------------------------------------ ids
    def nid(self, prefix: str, width: int = 6) -> str:
        return f"{prefix}{next(self._ctr[prefix]):0{width}d}"

    def rand_ts(self, start: datetime | None = None, end: datetime | None = None) -> datetime:
        start, end = start or self.cfg.start, end or self.end
        return start + timedelta(seconds=self.r.uniform(0, (end - start).total_seconds()))

    def day_ts(self, day: datetime) -> datetime:
        """A time on `day` following a human daily rhythm (peaks late morning + evening)."""
        hour = int(np.clip(self.rng.choice([10, 13, 19, 21]) + self.rng.normal(0, 2), 6, 23))
        return day.replace(hour=hour, minute=self.r.randrange(60), second=self.r.randrange(60))

    # ----------------------------------------------------------- factories
    def new_ip(self, kind: str, city: str | None = None) -> str:
        first = {
            "residential": 49,
            "mobile_cgnat": 100,
            "public_wifi": 103,
            "corporate": 115,
            "datacenter": 34,
            "vpn": 185,
        }[kind]
        while True:
            ip = f"{first}.{self.r.randrange(256)}.{self.r.randrange(256)}.{self.r.randrange(1, 255)}"
            if ip not in self.ips:
                break
        self.ips[ip] = {
            "ip": ip,
            "kind": kind,
            "city": city or self.r.choice(CITIES),
            "country": "IN" if kind not in ("vpn", "datacenter") else self.r.choice(["IN", "NL", "US", "SG", "DE"]),
        }
        return ip

    def new_device(self, os_: str | None = None, emulator: bool = False) -> str:
        d = self.nid("D")
        self.devices[d] = {
            "device_id": d,
            "os": os_ or self.r.choice(["android", "android", "ios", "web"]),
            "emulator": emulator,
        }
        return d

    def new_customer(
        self, *, kind="person", city=None, name=None, phone=None, email=None, address=None, dob=None, created_at=None
    ) -> str:
        c = self.nid("C")
        first, last = self.r.choice(FIRST), self.r.choice(LAST)
        city = city or self.r.choice(CITIES)
        self.customers[c] = {
            "customer_id": c,
            "kind": kind,
            "name": name or f"{first} {last}",
            "phone": phone or f"+91{self.r.randrange(70000, 99999)}{self.r.randrange(10000, 99999)}",
            "email": email
            or f"{first.lower()}.{last.lower()}{self.r.randrange(1, 999)}@{self.r.choice(EMAIL_DOMAINS)}",
            "address": address
            or f"{self.r.randrange(1, 400)}, {self.r.choice(['MG Road', 'Link Rd', 'Station Rd', 'Park St', 'Hill Rd', 'Lake View'])}, {city}",
            "city": city,
            "dob": dob or f"{self.r.randrange(1960, 2004)}-{self.r.randrange(1, 13):02d}-{self.r.randrange(1, 29):02d}",
            "created_at": created_at or (self.cfg.start - timedelta(days=int(self.rng.integers(60, 3000)))),
        }
        return c

    def new_account(self, customer_id: str, opened_at=None, acct_type="savings", with_card=True) -> str:
        a = self.nid("A")
        self.accounts[a] = {
            "account_id": a,
            "customer_id": customer_id,
            "type": acct_type,
            "opened_at": opened_at or self.customers[customer_id]["created_at"],
        }
        self._acct_by_cust[customer_id].append(a)
        if with_card:
            k = self.nid("K")  # tokenised card id, never a real PAN
            self.cards[k] = {"card_id": k, "account_id": a, "network": self.r.choice(["VISA", "MASTERCARD", "RUPAY"])}
            self._card_by_acct[a] = k
        return a

    def accounts_of(self, c: str) -> list[str]:
        return self._acct_by_cust[c]

    def card_of(self, a: str) -> str | None:
        return self._card_by_acct.get(a)

    # -------------------------------------------------------------- events
    def login(self, c, device, ip, ts, success=True):
        self.logins.append(
            {
                "login_id": self.nid("L", 8),
                "customer_id": c,
                "device_id": device,
                "ip": ip,
                "ts": ts,
                "success": success,
            }
        )

    def txn(
        self,
        ts,
        src_account,
        amount,
        *,
        dst_account=None,
        merchant_id=None,
        channel="upi",
        card_id=None,
        device=None,
        ip=None,
        status="approved",
        is_fraud=False,
        ring_id=None,
        pattern=None,
    ):
        t = {
            "txn_id": self.nid("T", 8),
            "ts": ts,
            "src_account": src_account,
            "dst_account": dst_account,
            "merchant_id": merchant_id,
            "amount": round(float(amount), 2),
            "currency": self.cfg.currency,
            "channel": channel,
            "card_id": card_id,
            "device_id": device,
            "ip": ip,
            "status": status,
            "is_fraud": is_fraud,
            "ring_id": ring_id,
            "pattern": pattern,
        }
        self.txns.append(t)
        return t

    def acct_event(self, c, kind, ts, device=None, ip=None, detail=None, is_fraud=False, ring_id=None):
        self.account_events.append(
            {
                "event_id": self.nid("E", 8),
                "customer_id": c,
                "kind": kind,
                "ts": ts,
                "device_id": device,
                "ip": ip,
                "detail": detail,
                "is_fraud": is_fraud,
                "ring_id": ring_id,
            }
        )

    # ========================================================= population
    def build_population(self):
        cfg = self.cfg
        # shared infrastructure: carrier NAT + public wifi (thousands share these in real life)
        self.cgnat = {city: [self.new_ip("mobile_cgnat", city) for _ in range(6)] for city in CITIES}
        self.wifi = {city: [self.new_ip("public_wifi", city) for _ in range(4)] for city in CITIES}
        for ip in itertools.chain(*self.cgnat.values(), *self.wifi.values()):
            self.traps["shared_carrier_or_wifi_ip"].add(ip)

        # merchants
        cats = list(MERCHANT_CATS)
        for _ in range(cfg.n_merchants):
            m = self.nid("M", 5)
            cat = self.r.choice(cats)
            self.merchants[m] = {
                "merchant_id": m,
                "category": cat,
                "risk": MERCHANT_CATS[cat][2],
                "city": self.r.choice(CITIES),
            }
        self.merch_by_cat = defaultdict(list)
        for m, v in self.merchants.items():
            self.merch_by_cat[v["category"]].append(m)

        # employers (payroll fan-out trap) and shopkeepers (fan-in trap)
        self.employers = []
        for _ in range(cfg.n_employers):
            c = self.new_customer(
                kind="business",
                name=f"{self.r.choice(LAST)} {self.r.choice(['Tech', 'Industries', 'Logistics', 'Pharma', 'Foods'])} Pvt Ltd",
            )
            a = self.new_account(c, acct_type="current", with_card=False)
            corp_ip = self.new_ip("corporate", self.customers[c]["city"])
            self.employers.append((c, a, corp_ip))
            self.traps["payroll_fan_out"].add(a)
        self.shopkeepers = []
        for _ in range(cfg.n_shopkeepers):
            c = self.new_customer(
                kind="business",
                name=f"{self.r.choice(FIRST)} {self.r.choice(['Kirana', 'Medicals', 'Chai Point', 'Tailors', 'Mobile Shop'])}",
            )
            a = self.new_account(c, acct_type="current", with_card=False)
            self.shopkeepers.append(a)
            self.traps["shopkeeper_fan_in"].add(a)
        # promo pool account used by referral programme
        pc = self.new_customer(kind="business", name="Bank Rewards Programme")
        self.promo_account = self.new_account(pc, acct_type="internal", with_card=False)

        # households of persons
        self.persons = []
        remaining = cfg.n_customers
        while remaining > 0:
            size = min(remaining, int(self.rng.choice([1, 1, 2, 2, 3, 4])))
            remaining -= size
            city = self.r.choice(CITIES)
            home_ip = self.new_ip("residential", city)
            last = self.r.choice(LAST)
            address = f"{self.r.randrange(1, 400)}, {self.r.choice(['Shanti Nagar', 'Green Park', 'Sector 21', 'Juhu Lane'])}, {city}"
            family_device = self.new_device("android") if size > 1 and self.r.random() < 0.35 else None
            members = []
            for _ in range(size):
                c = self.new_customer(city=city, address=address, name=f"{self.r.choice(FIRST)} {last}")
                self.new_account(c)
                if self.r.random() < 0.25:
                    self.new_account(c, acct_type="savings")
                own = [self.new_device() for _ in range(int(self.rng.choice([1, 1, 2])))]
                self.cust_devices[c] = own + ([family_device] if family_device else [])
                ips = [home_ip, self.r.choice(self.cgnat[city])]
                if self.r.random() < 0.3:
                    ips.append(self.r.choice(self.wifi[city]))
                self.cust_ips[c] = ips
                emp = self.r.choice(self.employers)
                self.customers[c]["employer_account"] = emp[1]
                if self.r.random() < 0.4:
                    self.cust_ips[c].append(emp[2])
                members.append(c)
                self.persons.append(c)
            if size > 1:
                for c in members:
                    self.traps["household_shared_address_ip"].add(c)
                    if family_device:
                        self.traps["household_shared_device"].add(c)
                for a_, b_ in itertools.combinations(members, 2):
                    self.friends[a_].add(b_)
                    self.friends[b_].add(a_)

        # social graph: mostly same-city friends
        by_city = defaultdict(list)
        for c in self.persons:
            by_city[self.customers[c]["city"]].append(c)
        for c in self.persons:
            pool = by_city[self.customers[c]["city"]]
            for f in self.r.sample(pool, k=min(len(pool), int(self.rng.integers(2, 7)))):
                if f != c:
                    self.friends[c].add(f)
                    self.friends[f].add(c)

    # ========================================================= behaviour
    def primary_account(self, c):
        return self.accounts_of(c)[0]

    def simulate_benign(self):
        cfg = self.cfg
        self._primary = {c: self.primary_account(c) for c in self.persons}
        for d in range(cfg.days):
            day = cfg.start + timedelta(days=d)
            # payroll on the 1st: employer fan-out
            if day.day == 1:
                for c in self.persons:
                    emp_acct = self.customers[c]["employer_account"]
                    self.txn(
                        day.replace(hour=9, minute=self.r.randrange(60)),
                        emp_acct,
                        self.rng.lognormal(10.6, 0.45),
                        dst_account=self._primary[c],
                        channel="neft",
                    )
            for c in self.persons:
                acct = self._primary[c]
                n_sessions = self.rng.poisson(0.8)
                for _ in range(n_sessions):
                    ts = self.day_ts(day)
                    dev, ip = self.r.choice(self.cust_devices[c]), self.r.choice(self.cust_ips[c])
                    self.login(c, dev, ip, ts)
                    roll = self.r.random()
                    if roll < 0.55:  # card spend
                        cat = self.r.choices(list(MERCHANT_CATS), weights=[20, 8, 12, 5, 4, 2, 5, 1.5, 1, 0.3, 4, 0.5])[
                            0
                        ]
                        mu, sd, _ = MERCHANT_CATS[cat]
                        self.txn(
                            ts + timedelta(minutes=self.r.randrange(1, 30)),
                            acct,
                            self.rng.lognormal(mu, sd),
                            merchant_id=self.r.choice(self.merch_by_cat[cat]),
                            channel="card",
                            card_id=self.card_of(acct),
                            device=dev,
                            ip=ip,
                        )
                    elif roll < 0.75:  # pay a shopkeeper (fan-in trap)
                        self.txn(
                            ts,
                            acct,
                            self.rng.lognormal(5.5, 0.7),
                            dst_account=self.r.choice(self.shopkeepers),
                            device=dev,
                            ip=ip,
                        )
                    elif roll < 0.92 and self.friends[c]:  # p2p with a friend
                        f = self.r.choice(sorted(self.friends[c]))
                        amt = self.rng.lognormal(6.8, 0.8)
                        self.txn(ts, acct, amt, dst_account=self._primary[f], device=dev, ip=ip)
                        if self.r.random() < 0.3:  # bill split pay-back -> benign 2-cycle
                            self.txn(
                                ts + timedelta(hours=self.r.randrange(1, 48)),
                                self._primary[f],
                                amt * self.r.uniform(0.4, 1.0),
                                dst_account=acct,
                                device=self.r.choice(self.cust_devices[f]),
                                ip=self.r.choice(self.cust_ips[f]),
                            )
                            self.traps["friends_bill_split_cycle"].add(acct)
            # benign "new phone" events — look like account takeover to naive rules
            for c in self.r.sample(self.persons, k=max(1, len(self.persons) // 600)):
                ts = self.day_ts(day)
                dev = self.new_device()
                self.cust_devices[c].append(dev)
                self.login(c, dev, self.r.choice(self.cust_ips[c]), ts)
                self.acct_event(c, "password_reset", ts + timedelta(minutes=2), device=dev)
                self.traps["benign_new_phone"].add(c)
                if self.r.random() < 0.5:  # and immediately a large purchase (new-phone excitement)
                    acct = self._primary[c]
                    self.txn(
                        ts + timedelta(minutes=20),
                        acct,
                        self.rng.lognormal(9.8, 0.4),
                        merchant_id=self.r.choice(self.merch_by_cat["electronics"]),
                        channel="card",
                        card_id=self.card_of(acct),
                        device=dev,
                        ip=self.cust_ips[c][0],
                    )
