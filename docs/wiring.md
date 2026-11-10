# Wiring

Everything below assumes a **5V WS2812B strip**, an **ESP8266 (NodeMCU)** running
WLED, and an **SN74AHCT125** level shifter.

> Read the safety notes at the bottom before connecting a power supply.

---

## 1. The whole system

```
   ┌──────────────┐                           ┌──────────────┐
   │     Mac      │   Wi-Fi (UDP 21324)       │  NodeMCU     │
   │ ambilight.py │ ────────────────────────▶ │  (WLED)      │
   │              │                           │              │
   │              │   ── or USB serial ──▶    │              │
   └──────────────┘                           └──────┬───────┘
          ▲                                          │ D4 (GPIO2), 3.3V data
          │ http://<mac-ip>:8080                     ▼
   ┌──────┴───────┐                           ┌──────────────┐
   │    Phone     │                           │ SN74AHCT125  │  level shifter
   │  (browser)   │                           │  3.3V ──▶ 5V │
   └──────────────┘                           └──────┬───────┘
                                                     │ 5V data
                                                     ▼
                                              ┌──────────────┐
                                              │  WS2812B     │
                                              │  240 LEDs    │
                                              └──────────────┘
                                                     ▲
                                              ┌──────┴───────┐
                                              │  5V PSU      │
                                              │  ≥ 10 A      │
                                              └──────────────┘
```

---

## 2. Minimum wiring (bench test only, short strip)

Acceptable for trying things out with a handful of LEDs. **Not** for your 4m run.

```
   NodeMCU                         WS2812B strip
   ┌─────────┐                     ┌──────────────┐
   │     D4  ├────────────────────▶│ DIN          │
   │         │                     │              │
   │     GND ├─────────────────────┤ GND          │
   │     VIN ├─────────────────────┤ +5V          │
   └─────────┘                     └──────────────┘
```

Why this is out of spec: D4 puts out 3.3V, the strip wants ≥3.5V. And the
NodeMCU's USB supply cannot feed more than a few dozen LEDs.

---

## 3. Correct wiring (what you should build)

```
                    ┌───────────────────────────────┐
                    │        5V PSU  (≥10 A)        │
                    └───┬───────────────────────┬───┘
                     +5V│                       │GND
        ┌───────────────┴───────┐               │
        │                       │               │
        │   ┌───────────────────┼───────────────┼─────────────┐
        │   │                   │               │             │
        ▼   ▼                   ▼               ▼             ▼
   ┌─────────────┐     ┌──────────────┐    ┌──────────┐  ┌─────────┐
   │  SN74AHCT125│     │   NodeMCU    │    │ WS2812B  │  │ WS2812B │
   │             │     │              │    │  start   │  │   end   │
   │ VCC ◀── +5V │     │ VIN ◀── +5V  │    │ +5V  GND │  │ +5V GND │  ← injection
   │ GND ◀── GND │     │ GND ◀── GND  │    └────┬─────┘  └─────────┘
   │             │     │              │         │
   │ 1A  ◀───────┼─────┤ D4 (GPIO2)   │         │
   │ 1Y  ────────┼─────┼──────────────┼────────▶│ DIN  (through 330Ω)
   │ 1OE ◀── GND │     └──────────────┘         │
   └─────────────┘                              │
                                         ┌──────┴──────┐
                                         │ 1000µF cap  │  across +5V / GND
                                         └─────────────┘
```

### Connection table

| From | To | Notes |
|---|---|---|
| PSU +5V | Strip +5V (start) | Main power feed |
| PSU GND | Strip GND (start) | |
| PSU +5V | NodeMCU VIN | Do **not** also power it over USB |
| PSU GND | NodeMCU GND | |
| PSU +5V | SN74AHCT125 VCC (pin 14) | Shifter must run at 5V |
| PSU GND | SN74AHCT125 GND (pin 7) | |
| NodeMCU D4 (GPIO2) | SN74AHCT125 1A (pin 2) | 3.3V data in |
| SN74AHCT125 1Y (pin 3) | Strip DIN | 5V data out, via 330Ω |
| SN74AHCT125 1OE (pin 1) | GND | Enables the buffer (active low) |
| PSU +5V / GND | Strip far end | **Power injection** |

### Why each extra part is there

| Part | Reason for it |
|---|---|
| **SN74AHCT125** | Lifts data from 3.3V to 5V so the strip reliably sees a HIGH |
| **330Ω resistor** | Damps ringing on the data line; protects the first LED |
| **1000µF capacitor** | Absorbs the inrush when the strip switches on |
| **Power injection** | Stops the far end going dim and red from voltage drop |
| **Common ground** | Without it the data signal has no reference and nothing works |

---

## 4. Power injection over 4 metres

Voltage drops along the copper, so the end of a long strip browns out:

```
      PSU
       │
   ┌───┴────────────────────────────────────────────────┐
   │                                                    │
   ▼                         ▼                          ▼
 ┌──────────┬────────────────┬─────────────────┬────────────┐
 │ LED 0    │                │ LED 120         │   LED 239  │
 └──────────┴────────────────┴─────────────────┴────────────┘
   inject            inject (middle)              inject
```

Feed +5V and GND at the start, the middle and the end. Data still only enters
once, at LED 0, and flows along the strip by itself.

---

## 5. ESP8266 pin notes

| Pin | Usable for LEDs? | Note |
|---|---|---|
| **D4 / GPIO2** | Yes — the monitor strip | Hardware-driven (UART1); WLED's default |
| **TX / GPIO1** | Yes — the bed strip (section 7) | Hardware-driven (UART0); **turns off USB serial output** |
| RX / GPIO3 | Only for a short strip | Uses 5× the memory per LED; WLED advises under ~50 LEDs |
| D1, D2, D5, D6, D7 | Last resort | Bit-banged: slow, and can upset Wi-Fi on an ESP8266 |
| D0 / GPIO16 | No | Cannot do the required timing |
| D3, D8 | Avoid | Boot-mode pins; strip may stop the board booting |

If you plan to use the **USB cable** output, keep GPIO1 and GPIO3 free. Source:
[WLED multi-strip](https://kno.wled.ge/features/multi-strip/).

---

## 6. Before powering on

- [ ] PSU rated above your worst case — 240 LEDs at full white is ~14.4A
- [ ] All grounds joined: PSU, NodeMCU, shifter, strip
- [ ] NodeMCU is **not** fed from USB and the PSU at the same time
- [ ] Data goes into **DIN**, not DOUT — strips are directional, check the arrows
- [ ] Capacitor polarity correct (the striped leg is negative)
- [ ] In WLED, set **Config ▸ LED Preferences ▸ Max current** to match your PSU
- [ ] Start with brightness low and confirm before turning it up

---

## 7. A second strip round the bed (same board, same shifter, same adapter)

The SN74AHCT125 has four channels and the monitor strip uses only channel 1,
so the bed strip takes **channel 4**, driven from the board's **RX (GPIO3)**,
a hardware-driven LED pin. The bed strip is powered from the **same 5V
adapter** as the monitor strip.

```
                 ┌───────────────────────┐
                 │       NodeMCU         │
                 │ VIN ◀── +5V           │
                 │ GND ◀── GND           │
                 │ D4 GPIO2     RX GPIO3 │
                 └─────┬───────────┬─────┘
                       │ 3.3V      │ 3.3V
                 ┌─────▼───────────▼─────┐
                 │      SN74AHCT125      │   14 VCC ◀── +5V
                 │ pin 2 1A   pin 12 4A  │    7 GND ◀── GND
                 │ pin 3 1Y   pin 11 4Y  │    1 1OE, 13 4OE ◀── GND
                 └─────┬───────────┬─────┘
                       │ 5V        │ 5V
                     330Ω        330Ω
                       ▼           ▼
            ┌──────────────┐   ┌──────────────┐
            │ monitor strip│   │  bed strip   │◀── 1000µF across +5V/GND
            │ DIN +5V GND  │   │ DIN +5V GND  │
            └──────┬───┬───┘   └──────┬───┬───┘
                   └───┴──────┬───────┴───┘   +5V and GND from the one adapter
                       ┌──────▼───────┐
                       │ 5V 4A adapter│
                       └──────────────┘
```

### Connection table (new wires only)

| From | To | Notes |
|---|---|---|
| NodeMCU RX (GPIO3) | SN74AHCT125 4A (pin 12) | 3.3V data in |
| SN74AHCT125 4OE (pin 13) | GND | Enables channel 4 (active low) |
| SN74AHCT125 4Y (pin 11) | 330Ω, then bed strip DIN | Resistor at the shifter end |
| Adapter +5V / GND | Bed strip +5V / GND | Proper power wire; jumpers carry about 1 A |
| Bed strip GND | Common GND | Same cable as the data wire |
| 1000µF capacitor | Bed strip +5V / GND, at its start | Striped leg to GND |
| 2A, 3A (pins 5, 9) | GND | Parks the unused channels |
| 2OE, 3OE (pins 4, 10) | +5V | Switches the unused channels off |

### RX notes

- Unplug the USB cable while the bed is on RX: the NodeMCU's USB-serial chip
  shares that pin and would fight the LED data.
- With the bed on RX, USB serial output and Wi-Fi setup over USB are off.
  Flashing over USB still works.
- RX uses more memory per LED than other pins: set the bed's real LED count.
- Another pin: `/api/bed/setup?pin=5` (D1), or 4, 12, 13, 14.

### Current limits on one adapter

The hub holds the monitor strip to 2.8 A and the bed to 1.0 A (3.8 A on a 4 A
adapter), and sets the board to limit each output separately.
