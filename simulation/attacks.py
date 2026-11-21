"""Fraud attack playbooks, modelled on publicly documented typologies
(FATF money-mule red flags, card-testing / enumeration attacks, synthetic-identity
bust-out, promo/referral abuse, round-tripping).

Each playbook writes events into the World and registers a ring with full ground
truth: actor accounts, victim accounts, devices, IPs, and a role per account.
"""

from __future__ import annotations

from datetime import timedelta

from .world import World


def _ring(w: World, pattern: str, idx: int, narrative: str) -> dict:
    ring = {
        "ring_id": f"RING-{pattern.upper()}-{idx}",
        "pattern": pattern,
        "narrative": narrative,
        "roles": {},
        "victim_accounts": set(),
        "victim_customers": set(),
        "victim_cards": set(),
        "devices": set(),
        "ips": set(),
        "start": None,
        "end": None,
    }
    w.rings.append(ring)
    return ring


def _role(ring, account, role):
    ring["roles"][account] = role


def _span(ring, ts):
    ring["start"] = ts if ring["start"] is None or ts < ring["start"] else ring["start"]
    ring["end"] = ts if ring["end"] is None or ts > ring["end"] else ring["end"]


def _recruit(w: World, k: int) -> list[str]:
    """Pick existing benign people to act as mules (money-mule recruitment via 'job offers')."""
    pool = [c for c in w.persons if c not in w.used and c not in w.traps["benign_new_phone"]]
    picked = w.r.sample(pool, k)
    w.used.update(picked)
    return picked


def _fresh_identity(w: World, created_at, **kw) -> tuple[str, str]:
    c = w.new_customer(created_at=created_at, **kw)
    w.used.add(c)
    return c, w.new_account(c, opened_at=created_at)


# --------------------------------------------------------------------------- 1
def mule_network(w: World, idx: int):
    """Layering network fed by (a) structured cash placement and (b) ATO drains.
    controller -> tier1 mules -> tier2 mules -> cash-out accounts -> ATM."""
    cfg = w.cfg
    ring = _ring(
        w,
        "mule_network",
        idx,
        "Recruited mules receive stolen/placed funds and forward them within hours, keeping a commission.",
    )
    t0 = w.rand_ts(cfg.start + timedelta(days=8), cfg.start + timedelta(days=25))
    ctrl_devs = [w.new_device("android"), w.new_device("web")]
    ctrl_ips = [w.new_ip("vpn") for _ in range(3)] + [w.new_ip("datacenter")]
    ring["devices"].update(ctrl_devs)
    ring["ips"].update(ctrl_ips)

    controller, ctrl_acct = _fresh_identity(w, t0 - timedelta(days=w.r.randint(5, 20)))
    w.cust_devices[controller] = ctrl_devs
    _role(ring, ctrl_acct, "controller")
    tier1 = [w.primary_account(c) for c in _recruit(w, w.r.randint(4, 6))]
    tier2 = [w.primary_account(c) for c in _recruit(w, w.r.randint(3, 4))]
    cashout = [_fresh_identity(w, t0 - timedelta(days=w.r.randint(2, 10)))[1] for _ in range(2)]
    for a in tier1:
        _role(ring, a, "mule_tier1")
    for a in tier2:
        _role(ring, a, "mule_tier2")
    for a in cashout:
        _role(ring, a, "cash_out")

    # operational control: controller logs into mule accounts from its own kit
    for a in tier1 + tier2 + cashout:
        c = w.accounts[a]["customer_id"]
        if a in cashout or w.r.random() < 0.55:
            ts = t0 - timedelta(hours=w.r.randint(1, 72))
            w.login(c, w.r.choice(ctrl_devs), w.r.choice(ctrl_ips), ts)

    # ATO victims whose funds are drained into tier-1 mules
    ato_inflows = _ato_drains(w, ring, tier1, ctrl_devs, ctrl_ips, t0)

    waves = w.r.randint(4, 7)
    t = t0
    for wave in range(waves):
        t = t + timedelta(days=w.r.uniform(1, 5))
        inbound: dict[str, float] = {}
        # (a) structured placement: cash deposits just under the reporting threshold
        for a in w.r.sample(tier1, k=w.r.randint(1, 3)):
            for _ in range(w.r.randint(1, 3)):
                amt = cfg.reporting_threshold * w.r.uniform(0.86, 0.995)
                ts = t + timedelta(minutes=w.r.randint(0, 600))
                w.txn(
                    ts,
                    None,
                    amt,
                    dst_account=a,
                    channel="cash_deposit",
                    is_fraud=True,
                    ring_id=ring["ring_id"],
                    pattern="structuring",
                )
                inbound[a] = inbound.get(a, 0) + amt
                _span(ring, ts)
        # (b) one ATO drain feeds each wave
        if wave < len(ato_inflows):
            a, amt, ts = ato_inflows[wave]
            inbound[a] = inbound.get(a, 0) + amt
            t = max(t, ts)

        # pass-through: tier1 -> tier2 -> cash-out, each hop within hours, minus commission
        def forward(src_amounts, targets, hop_hours, pattern):
            out: dict[str, float] = {}
            for src, amt in src_amounts.items():
                send = amt * (1 - w.r.uniform(0.02, 0.05))
                splits = w.r.sample(targets, k=min(len(targets), w.r.randint(1, 2)))
                c = w.accounts[src]["customer_id"]
                for i, dst in enumerate(splits):
                    part = send / len(splits)
                    ts = t + timedelta(hours=w.r.uniform(*hop_hours))
                    dev = w.r.choice(ctrl_devs) if w.r.random() < 0.4 else w.r.choice(w.cust_devices[c])
                    ip = w.r.choice(ctrl_ips) if dev in ctrl_devs else w.r.choice(w.cust_ips.get(c) or ctrl_ips)
                    w.txn(
                        ts,
                        src,
                        part,
                        dst_account=dst,
                        channel="imps",
                        device=dev,
                        ip=ip,
                        is_fraud=True,
                        ring_id=ring["ring_id"],
                        pattern=pattern,
                    )
                    out[dst] = out.get(dst, 0) + part
                    _span(ring, ts)
            return out

        t2 = forward(inbound, tier2, (0.5, 6), "layering")
        t3 = forward(t2, cashout, (6, 14), "layering")
        for a, amt in t3.items():  # ATM cash-out in chunks
            left = amt
            while left > 500:
                chunk = min(left, w.r.choice([10_000, 20_000, 25_000]))
                ts = t + timedelta(hours=w.r.uniform(14, 30))
                w.txn(
                    ts,
                    a,
                    chunk,
                    channel="atm",
                    device=None,
                    ip=None,
                    is_fraud=True,
                    ring_id=ring["ring_id"],
                    pattern="cash_out",
                )
                left -= chunk
                _span(ring, ts)


def _ato_drains(w: World, ring, tier1, ctrl_devs, ctrl_ips, t0):
    """Account takeover: credential stuffing -> new device -> password reset ->
    add beneficiary (a mule) -> drain. Returns [(mule_account, amount, ts)]."""
    out = []
    victims = _recruit(w, w.cfg.attacks.ato_victims_per_mule_net)
    for i, v in enumerate(victims):
        acct = w.primary_account(v)
        ring["victim_customers"].add(v)
        ring["victim_accounts"].add(acct)
        ts = t0 + timedelta(days=i * w.r.uniform(1, 4), hours=w.r.randint(0, 5))
        dev, ip = w.r.choice(ctrl_devs), w.r.choice(ctrl_ips)
        for k in range(w.r.randint(1, 4)):  # failed credential-stuffing attempts
            w.login(v, dev, ip, ts - timedelta(minutes=10 - k), success=False)
        w.login(v, dev, ip, ts)
        w.acct_event(v, "password_reset", ts + timedelta(minutes=1), dev, ip, is_fraud=True, ring_id=ring["ring_id"])
        mule = w.r.choice(tier1)
        w.acct_event(
            v,
            "beneficiary_added",
            ts + timedelta(minutes=3),
            dev,
            ip,
            detail=mule,
            is_fraud=True,
            ring_id=ring["ring_id"],
        )
        total = 0.0
        for j in range(w.r.randint(1, 3)):
            amt = w.rng.lognormal(10.6, 0.4)
            tts = ts + timedelta(minutes=6 + j * w.r.randint(2, 15))
            w.txn(
                tts,
                acct,
                amt,
                dst_account=mule,
                channel="imps",
                device=dev,
                ip=ip,
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="ato_drain",
            )
            total += amt
            _span(ring, tts)
        out.append((mule, total, ts))
    return out


# --------------------------------------------------------------------------- 2
def synthetic_identity(w: World, idx: int):
    """Fabricated identities share fragments of PII, behave politely for weeks
    (build-up), then max out spend on resellable goods on the same day (bust-out)."""
    cfg = w.cfg
    ring = _ring(
        w,
        "synthetic_identity",
        idx,
        "Identities stitched from a small pool of phones/addresses build history, then bust out together.",
    )
    n = w.r.randint(8, 14)
    phones = [f"+91{w.r.randrange(60000, 69999)}{w.r.randrange(10000, 99999)}" for _ in range(max(2, n // 3))]
    city = w.r.choice(["Mumbai", "Delhi", "Bengaluru"])
    addresses = [
        f"Flat {w.r.randrange(1, 40)}, {w.r.choice(['Sunrise CHS', 'Om Residency', 'Galaxy Apts'])}, {city}"
        for _ in range(2)
    ]
    devs = [w.new_device("android", emulator=True), w.new_device("web")]
    ips = [w.new_ip("datacenter"), w.new_ip("datacenter"), w.new_ip("vpn")]
    ring["devices"].update(devs)
    ring["ips"].update(ips)
    born = w.rand_ts(cfg.start - timedelta(days=20), cfg.start + timedelta(days=8))
    bust = cfg.start + timedelta(days=w.r.randint(int(cfg.days * 0.7), cfg.days - 3))
    handle = w.r.choice(["rk", "sam", "dev", "raj", "nik"])
    accts = []
    for i in range(n):
        created = born + timedelta(hours=w.r.uniform(0, 120))
        c, a = _fresh_identity(
            w,
            created,
            city=city,
            phone=w.r.choice(phones),
            address=w.r.choice(addresses),
            email=f"{handle}{w.r.randrange(1000, 9999)}@{w.r.choice(['gmail.com', 'proton.me'])}",
        )
        w.cust_devices[c] = devs
        w.cust_ips[c] = ips + ([w.r.choice(w.cgnat[city])] if w.r.random() < cfg.camouflage else [])
        _role(ring, a, "synthetic_identity")
        accts.append((c, a))
        w.login(c, w.r.choice(devs), w.r.choice(ips), created)
    collector, coll_acct = _fresh_identity(w, born)
    _role(ring, coll_acct, "cash_out")

    # build-up: small polite activity, some circular p2p between identities
    for c, a in accts:
        t = w.customers[c]["created_at"] + timedelta(days=1)
        while t < bust - timedelta(days=1):
            t += timedelta(days=w.r.uniform(1.5, 4))
            dev, ip = w.r.choice(w.cust_devices[c]), w.r.choice(w.cust_ips[c])
            w.login(c, dev, ip, t)
            cat = w.r.choice(["grocery", "digital_goods", "restaurant"])
            w.txn(
                t,
                a,
                w.rng.lognormal(5.8, 0.5),
                merchant_id=w.r.choice(w.merch_by_cat[cat]),
                channel="card",
                card_id=w.card_of(a),
                device=dev,
                ip=ip,
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="build_up",
            )
            if w.r.random() < 0.3:
                peer = w.r.choice(accts)[1]
                if peer != a:
                    w.txn(
                        t + timedelta(minutes=5),
                        a,
                        w.rng.lognormal(6.5, 0.4),
                        dst_account=peer,
                        device=dev,
                        ip=ip,
                        is_fraud=True,
                        ring_id=ring["ring_id"],
                        pattern="build_up",
                    )
            _span(ring, t)
    # bust-out
    for c, a in accts:
        dev, ip = w.r.choice(devs), w.r.choice(ips)
        for _ in range(w.r.randint(3, 6)):
            ts = bust + timedelta(hours=w.r.uniform(0, 30))
            cat = w.r.choice(["electronics", "gift_cards", "jewellery"])
            w.txn(
                ts,
                a,
                w.rng.lognormal(10.2, 0.4),
                merchant_id=w.r.choice(w.merch_by_cat[cat]),
                channel="card",
                card_id=w.card_of(a),
                device=dev,
                ip=ip,
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="bust_out",
            )
            _span(ring, ts)
        w.txn(
            bust + timedelta(hours=w.r.uniform(2, 20)),
            a,
            w.rng.lognormal(10.0, 0.3),
            dst_account=coll_acct,
            device=dev,
            ip=ip,
            is_fraud=True,
            ring_id=ring["ring_id"],
            pattern="bust_out",
        )


# --------------------------------------------------------------------------- 3
def card_testing(w: World, idx: int):
    """Enumeration attack: validate stolen cards with tiny auths through rotating
    proxies, then monetise the live ones on high-resale goods."""
    cfg = w.cfg
    ring = _ring(
        w,
        "card_testing",
        idx,
        "Bot validates stolen cards with micro-auths via rotating proxies, then cashes out live cards.",
    )
    devs = [w.new_device("web"), w.new_device("android", emulator=True)]
    proxies = [w.new_ip("datacenter") for _ in range(w.r.randint(10, 20))]
    ring["devices"].update(devs)
    ring["ips"].update(proxies)
    test_merchants = w.r.sample(w.merch_by_cat["donations"] + w.merch_by_cat["digital_goods"], 2)
    t0 = w.rand_ts(cfg.start + timedelta(days=5), cfg.start + timedelta(days=cfg.days - 7))
    victims = _recruit(w, w.r.randint(40, 80))
    window_h = 1.5 + 10 * cfg.camouflage  # patient attackers spread the run out
    live = []
    for v in victims:
        acct = w.primary_account(v)
        card = w.card_of(acct)
        ring["victim_customers"].add(v)
        ring["victim_accounts"].add(acct)
        ring["victim_cards"].add(card)
        dead = w.r.random() < 0.55
        for _ in range(w.r.randint(1, 3)):
            ts = t0 + timedelta(hours=w.r.uniform(0, window_h))
            w.txn(
                ts,
                acct,
                w.r.uniform(1, 10),
                merchant_id=w.r.choice(test_merchants),
                channel="card_not_present",
                card_id=card,
                device=w.r.choice(devs),
                ip=w.r.choice(proxies),
                status="declined" if dead else "approved",
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="card_test",
            )
            _span(ring, ts)
        if not dead:
            live.append((acct, card))
    for acct, card in live:
        for _ in range(w.r.randint(1, 2)):
            ts = t0 + timedelta(hours=w.r.uniform(18, 72))
            w.txn(
                ts,
                acct,
                w.rng.lognormal(9.6, 0.5),
                merchant_id=w.r.choice(w.merch_by_cat["electronics"] + w.merch_by_cat["gift_cards"]),
                channel="card_not_present",
                card_id=card,
                device=w.r.choice(devs),
                ip=w.r.choice(proxies),
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="card_cashout",
            )
            _span(ring, ts)


# --------------------------------------------------------------------------- 4
def promo_abuse(w: World, idx: int):
    """One person farms referral bonuses with many accounts on a few (emulated)
    devices, funnelling every bonus to their real account."""
    cfg = w.cfg
    ring = _ring(
        w,
        "promo_abuse",
        idx,
        "A single abuser creates a referral chain of fake accounts on shared devices and sweeps bonuses.",
    )
    abuser = _recruit(w, 1)[0]
    coll = w.primary_account(abuser)
    _role(ring, coll, "collector")
    city = w.customers[abuser]["city"]
    devs = [w.new_device("android", emulator=w.r.random() < 0.7) for _ in range(w.r.randint(1, 3))]
    ips = w.cgnat[city][:2] + [w.new_ip("residential", city)]  # hides behind carrier NAT
    ring["devices"].update(devs)
    t = w.rand_ts(cfg.start + timedelta(days=3), cfg.start + timedelta(days=cfg.days - 10))
    referrer_c, n = abuser, w.r.randint(10, 25)
    for i in range(n):
        t += timedelta(minutes=w.r.uniform(5, 240) * (1 + 4 * cfg.camouflage))
        c, a = _fresh_identity(w, t, city=city, email=f"user{w.r.randrange(100000, 999999)}@tempmail.dev")
        _role(ring, a, "farmed_account")
        dev, ip = w.r.choice(devs), w.r.choice(ips)
        w.cust_devices[c] = [dev]
        w.cust_ips[c] = [ip]
        w.login(c, dev, ip, t)
        w.referrals.append(
            {
                "referrer_customer": referrer_c,
                "referee_customer": c,
                "ts": t,
                "is_fraud": True,
                "ring_id": ring["ring_id"],
            }
        )
        bonus = w.r.choice([150, 200, 250])
        for who in (referrer_c, c):
            w.txn(
                t + timedelta(minutes=2),
                w.promo_account,
                bonus,
                dst_account=w.primary_account(who),
                channel="internal",
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="promo_bonus",
            )
        w.txn(
            t + timedelta(hours=w.r.uniform(0.2, 20)),
            a,
            bonus,
            dst_account=coll,
            device=dev,
            ip=ip,
            is_fraud=True,
            ring_id=ring["ring_id"],
            pattern="promo_sweep",
        )
        _span(ring, t)
        referrer_c = c if w.r.random() < 0.6 else abuser  # chains and stars


def benign_referrals(w: World):
    """Real friends referring friends — includes households on one family device (trap)."""
    for c in w.r.sample(w.persons, k=len(w.persons) // 40):
        if w.friends[c]:
            f = w.r.choice(sorted(w.friends[c]))
            ts = w.rand_ts()
            w.referrals.append(
                {"referrer_customer": f, "referee_customer": c, "ts": ts, "is_fraud": False, "ring_id": None}
            )
            for who in (f, c):
                w.txn(ts, w.promo_account, 200, dst_account=w.primary_account(who), channel="internal")


# --------------------------------------------------------------------------- 5
def round_tripping(w: World, idx: int):
    """Shell companies with a common director cycle the same money to inflate
    turnover (e.g. to qualify for credit lines)."""
    cfg = w.cfg
    ring = _ring(
        w, "round_tripping", idx, "Shell firms with a shared director and registered address pass funds around a loop."
    )
    n = w.r.randint(4, 6)
    director_phone = f"+91{w.r.randrange(80000, 89999)}{w.r.randrange(10000, 99999)}"
    reg_addr = (
        f"Office {w.r.randrange(100, 999)}, {w.r.choice(['Trade Centre', 'Business Bay', 'Corporate Park'])}, Mumbai"
    )
    dev = w.new_device("web")
    ip = w.new_ip("corporate", "Mumbai")
    ring["devices"].add(dev)
    ring["ips"].add(ip)
    accts = []
    for i in range(n):
        c, a = _fresh_identity(
            w,
            cfg.start - timedelta(days=w.r.randint(90, 400)),
            kind="business",
            name=f"{w.r.choice(['Apex', 'Zenith', 'Orbit', 'Nova', 'Vertex', 'Prime'])} {w.r.choice(['Traders', 'Exim', 'Ventures', 'Infra'])} LLP",
            phone=director_phone,
            address=reg_addr if w.r.random() < 0.7 else None,
        )
        w.cust_devices[c] = [dev]
        w.cust_ips[c] = [ip]
        _role(ring, a, "shell_company")
        accts.append((c, a))
    t = cfg.start + timedelta(days=w.r.randint(2, 10))
    for cyc in range(w.r.randint(3, 6)):
        amt = w.r.uniform(800_000, 3_000_000)
        order = accts[:] if w.r.random() < 0.7 else w.r.sample(accts, len(accts))
        for i, (c, a) in enumerate(order):
            nxt = order[(i + 1) % len(order)][1]
            t += timedelta(hours=w.r.uniform(2, 30))
            w.login(c, dev, ip, t - timedelta(minutes=3))
            w.txn(
                t,
                a,
                amt,
                dst_account=nxt,
                channel="rtgs",
                device=dev,
                ip=ip,
                is_fraud=True,
                ring_id=ring["ring_id"],
                pattern="round_trip",
            )
            amt *= 1 - w.r.uniform(0.001, 0.005)
            _span(ring, t)
        t += timedelta(days=w.r.uniform(3, 10))
    # camouflage: some ordinary-looking supplier payments
    for c, a in accts:
        for _ in range(w.r.randint(2, 6)):
            w.txn(
                w.rand_ts(),
                a,
                w.rng.lognormal(10.5, 0.6),
                dst_account=w.r.choice(w.shopkeepers),
                channel="neft",
                device=dev,
                ip=ip,
            )
