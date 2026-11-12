# What to set in Nautobot and LibreNMS

Nautobot Maps shows what is already in **Nautobot**. It never changes anything there. So how good the map, the alert board and the customer status pages are depends on what is filled in in Nautobot, and, if you use it, **LibreNMS**.

This page lists everything the app reads, in one place:

- **[The basics](#the-basics-required)**: without these, there is nothing to show.
- **[Optional, by feature](#optional-by-feature)**: each one adds something. Skip what you don't need.
- **[LibreNMS](#librenms-optional)**: faster and more reliable up/down status.

Each item says **where** to set it, **what it does**, and the **app setting** it needs, if any. App settings go in the `.env` file ([every setting](configuration.md)).

## In short

| You want… | Set in Nautobot | App setting |
|---|---|---|
| A site on the map | Location → **Latitude** and **Longitude** | — |
| A device to be watched | Device → **Primary IPv4** (or IPv6) | — |
| A device to show as down | Device → **Status** `Offline` or `Failed`, or LibreNMS | `LIBRENMS_URL`, `LIBRENMS_API_TOKEN` |
| Customers on a site | Location → **Tenant** (and Relationships for more) | — |
| A note on the customer's status page | Location → **Description**; Tenant → **Description** | — |
| Important sites to alarm louder | Location → custom field **tier** | `SITE_TIER_FIELD`, `TIER_DEVICE_LEVEL` |
| A device's own alarm level | Device → custom field **criticality** | `DEVICE_CRITICALITY_FIELD` |
| A customer to see a device by name | Device → **Tenant** | `STATUS_PAGE_TENANT_DEVICES=true` |
| A customer to see their circuits | Circuit → **Tenant**, status **Active** | — |
| A customer to see only some roles at a shared site | Location → custom field **tenant_scope** (JSON) | `TENANT_SCOPE_FIELD` |
| Access points counted apart | Device → **Role** `Access Point` | `ACCESS_POINT_ROLES` |
| One board row per site, not per floor | **Location types** (e.g. Site › Building › Floor) | `ALERT_BOARD_SITE_LOCATION_TYPE` |
| Old or planned things hidden | Location / Device → **Status**, **Tags** | `ALERT_BOARD_EXCLUDED_*` |

## The basics (required)

### An API token for the app

- **Where:** Nautobot → your user → **Profile → API Tokens → Add**. Untick **Write enabled**: the app only reads.
- **What it needs to read:** locations, location types, devices, device types, manufacturers, roles, statuses, tags, cables, tenants, tenant groups, circuits and their terminations, relationships and their associations, and ASNs. A user that can view everything is simplest.
- **App setting:** `NAUTOBOT_URL=https://nautobot.example.com` and `NAUTOBOT_TOKEN=<the token>`.

### Sites on the map: coordinates

- **Where:** Location → Edit → **Latitude** and **Longitude**.
- **What it does:** a location with both is a pin on the map. Without them it is still on the alert board, but not on the map.
- **Check:** the map says "12 sites aren't on the map · **Check**" and lists the sites that are missing coordinates or have wrong ones (for example 0,0, or latitude and longitude swapped), each with a link to fix it in Nautobot ([more](operations.md#sites-with-missing-or-wrong-coordinates)).
- **Also shown, if filled in:** **Physical address** (under the site's name, and on the status page), **Description** (also on the status page: [a note about the site](#a-note-about-the-site)), **Facility**, **ASN** and **Time zone**.

### Devices that are watched: a primary IP

The app only judges a site by its **monitored** devices. Others are listed on the map, but never make a site red.

- **Where:** Device → Edit → **Primary IPv4** (or **Primary IPv6**).
- **What it does:** a device with a primary IP is monitored: it counts towards its site's alert level.
- **Access points** often get their address from DHCP and have no primary IP. They are monitored when a [wireless controller](sources.md#wireless-controllers-access-points) lists them instead.
- **Check:** the map's site panel says "Not monitored: no primary IP" next to a device that doesn't count.

### Up or down: the device status

- **Where:** Device → Edit → **Status**.
- **What it does:** `Offline`, `Failed` and `Decommissioning` count as **down**. Every other status counts as up.
- **With LibreNMS:** LibreNMS's up/down is added on top: a device LibreNMS sees as down is down, even when Nautobot says `Active` ([LibreNMS](#librenms-optional)).
- **Tip:** to stop a `Decommissioning` or `Planned` device from raising an alarm, hide it: `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES=decommissioning,planned` ([hiding things](#hiding-sites-and-devices)).

## Optional, by feature

### Customers (tenants)

#### The site's customer

- **Where:** Location → Edit → **Tenant**.
- **What it does:** the customer is shown under the site's name on the board and the map, can be filtered on, and gets the site on their [status page](status-page.md).

#### More than one customer on a site

- **Where:** a **Relationship** between Location and Tenant (Extensibility → Relationships), then link the tenants on each location.
- **What it does:** these tenants are shown next to the site's own tenant, and the site is on each of their status pages.
- **App setting:** `SITE_TENANT_RELATIONSHIPS=<relationship key or label>`. Leave it empty to use every Location ↔ Tenant relationship.

#### A note about the customer

- **Where:** Tenant → Edit → **Description**.
- **What it does:** an **(i)** next to the tenant on the board, the map and the wall view shows it, and the customer's status page shows it after their name ("Acme Corp – Retail bank, 24/7").
- **Careful:** customers can read it on their status page. Keep internal notes out.

#### A note about the site

- **Where:** Location → Edit → **Description**.
- **What it does:** the customer's status page shows it under the site's name ("Primary Scandinavian data centre"). Nothing is shown when it is empty.
- **Careful:** customers can read it. Keep internal notes out.

#### The customer's own devices, by name, on their status page

- **Where:** Device → Edit → **Tenant**. For a stack (virtual chassis), set it on the master.
- **What it does:** the customer's status page lists these devices by name, with Up, Down (since when) or Maintenance. Devices without a tenant, or with another one, are never named.
- **App setting:** `STATUS_PAGE_TENANT_DEVICES=true`.
- **Careful:** turning it on names every device that already has a tenant. Check first: Devices, filtered by Tenant, per customer ([more](status-page.md#the-tenants-own-devices)).

#### The customer's circuits on their status page

- **Where:** Circuit → Edit → **Tenant** = the customer, and **Status** = `Active`.
- **What it does:** the status page lists the circuit at its site: circuit ID, type and bandwidth (never the provider).
- **Which site:** where the circuit has a termination, or, without one, the site whose physical address is the circuit's description ([circuits by address](how-it-works.md#circuits-in-the-site-panel)).

#### Only some device roles for a customer at a shared site

At a site shared by several customers, each one can be shown only the devices in their roles: their state and counts leave out everyone else's equipment.

- **Where:** a custom field on Locations, type **JSON**, for example with the key `tenant_scope`. On each shared site, fill it in:

  ```json
  {"Acme Corp": ["Access Switch", "Firewall"]}
  ```

- **What it does:** Acme Corp's status page judges that site by its access switches and firewalls only. A customer without an entry sees the whole site; if none of their roles is at the site, the site is hidden for them.
- **App setting:** `TENANT_SCOPE_FIELD=tenant_scope` ([more](status-page.md#per-site-device-roles)).

### How serious an outage is

With nothing set, the share of a site's devices that are down decides: some down is **Low**, more than a quarter **Medium**, all **Critical**. These make it smarter. How they combine: [device and site criticality](alert-board.md#device-and-site-criticality).

#### Important sites: a tier

- **Where:** Extensibility → Custom Fields → Add: key `tier`, content type `dcim | location`, type **Selection**, choices `gold`, `silver`, `bronze`. Then pick a tier on each site that has one.
- **What it does:** the tier is shown by the site's name, and more important sites come first among sites at the same level.
- **App settings:**
  - `SITE_TIER_FIELD=tier`
  - `TIER_DEVICE_LEVEL=gold:critical,silver:medium`: any device down at a gold site is Critical, at a silver site at least Medium.
  - Optional: `NOTIFY_MIN_LEVEL_BY_TIER` to notify sooner for important sites ([notifications](notifications.md)).

#### One device's own level

- **Where:** Extensibility → Custom Fields → Add: key `criticality`, content type `dcim | device`, type **Selection**, choices `critical`, `medium`, `low`. Then set it on the devices that need it.
- **What it does:** when that device is down, its site is at least that level. It wins over the site's tier, so a `low` device on a gold site doesn't make it Critical.
- **App setting:** `DEVICE_CRITICALITY_FIELD=criticality`.

#### Core devices, by their role

- **Where:** Device → **Role**. Nothing new to add.
- **What it does:** a down device whose role name contains `core`, `spine`, `distribution`, `router` or `gateway` makes its site **Critical**, and the site's other down devices are marked "Likely caused by" it.
- **Unless:** the device has its own **criticality** set (above): that always wins, so a core router set to `low` gives Low. An operator can also mark a device "not critical" in the app, which turns this rule off for it.
- **App setting (to change the words):** `CRITICAL_ROLE_KEYWORDS=core,spine,router`, or different words per location type in `CRITICALITY_RULES_FILE`.

**Editing a tier or a criticality field doesn't page anyone**, even when it changes a site's level: only a device going down or coming back does. **Changing a role can page**: renaming a down device's role to one with a core word (for example Access Switch → Core Router) can make its site Critical and send a notification.

### Access points

- **Where:** Device → **Role** `Access Point` (or your own role names).
- **What it does:** access points are counted apart ("12 network devices · 40 access points"). With a wireless controller, a site with only access points down is not an alarm: it is shown under **Wireless**, and is not notified.
- **Unless:** an access point has its own **criticality** set in Nautobot, or is marked critical in the app. Then it is an ordinary alarm at that level when down, and can be notified.
- **App setting:** `ACCESS_POINT_ROLES=Access Point,AP` when your roles are named differently (capitals don't matter).
- **Matching to a controller:** by **Serial**, else by **Name** (the part before the first dot). Fill in the serial number for the surest match ([wireless controllers](sources.md#wireless-controllers-access-points)).
- **Which switch an access point is on:** a **Cable** in Nautobot between the access point and the switch is used when the controller doesn't say.

### The alert board's rows

#### One row per site, not per building or floor

- **Where:** **Location types** in a hierarchy, for example Region › Country › Site › Building › Floor.
- **What it does:** the board shows one row per **Site**, and devices in its buildings and floors count towards it. A down device says where it is ("Building A › Floor 2").
- **App setting:** `ALERT_BOARD_SITE_LOCATION_TYPE=Site` ([more](alert-board.md#one-row-per-site)).

#### A site's country

- **Where:** a location of type **Country** above the site (for example `DNK`). Otherwise the last part of the address is used when it is clearly a country.
- **What it does:** the country is searchable on the board, and is in the availability CSV.
- **App setting:** `COUNTRY_LOCATION_TYPE=Country` when your location type has another name.

#### Hiding sites and devices

Hidden sites and devices don't count and aren't listed on the board. They stay on the map.

| Hide… | Set in Nautobot | App setting |
|---|---|---|
| Sites of a type (default: graveyard, warehouse) | Location → **Location type** | `ALERT_BOARD_EXCLUDED_LOCATION_TYPES` |
| Sites with a status (e.g. Planned) | Location → **Status** | `ALERT_BOARD_EXCLUDED_LOCATION_STATUSES` |
| Sites with a tag | Location → **Tags** | `ALERT_BOARD_EXCLUDED_LOCATION_TAGS` |
| Devices with a status (e.g. Decommissioning) | Device → **Status** | `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES` |
| One device LibreNMS shouldn't alert for | In LibreNMS, see [below](#a-device-you-dont-want-alarms-for) | none |

In the status settings, `null` means "no status set". Example: `ALERT_BOARD_EXCLUDED_DEVICE_STATUSES=null,decommissioning,planned` ([more](alert-board.md#hiding-sites-and-devices)).

### Circuits on the map

- **Best:** a **circuit termination** at the location. The site panel lists the circuit as terminated there.
- **Until then:** a circuit with no termination whose **Description** is the site's physical address is listed as *By address*. Write the address the same way as on the location, for example `Roersvej 33, 5000 Odense C`.
- **Worklist:** **Circuits** in the ☰ menu lists every circuit without a termination, grouped by the site its description points to ([circuit mapping](how-it-works.md#circuit-mapping)).

## LibreNMS (optional)

Without LibreNMS, up or down comes only from the device status in Nautobot. With it, a device that stops answering shows as down on its own.

### Connect it

- **Where (in LibreNMS):** the gear menu → **API → API Settings → Create API access token**.
- **App settings:** `LIBRENMS_URL=https://librenms.example.com` and `LIBRENMS_API_TOKEN=<the token>` ([more](configuration.md#librenms-optional)).

### How devices are matched

A LibreNMS device is matched to a Nautobot device by:

1. its **hostname**, without the domain and ignoring capitals (`cph-core-rt01.example.com` in LibreNMS = `cph-core-rt01` in Nautobot), or
2. when the LibreNMS hostname is an IP address: the Nautobot device's **primary IP**.

So the easiest is to give devices the same name in both. A device must still have a primary IP in Nautobot to be monitored.

### A device you don't want alarms for

If you have told LibreNMS not to alert for a device, the alert board leaves it out too: it isn't counted, isn't listed and isn't notified, even when it is down.

- **Where (in LibreNMS):** the device → **⚙ Edit → Device Settings**, any one of:
  - **Disable polling and alerting**
  - **Disable alerting**
  - **Ignore alert tag**
- **Shows up:** at the app's next LibreNMS read (every 5 minutes by default), or at once with an [alert transport](#down-within-seconds-an-alert-transport) when LibreNMS sends one for the device.
- **In the app:** the map's site panel still lists the device, with why: "Not monitored: alerting disabled in LibreNMS".
- **Good to know:** if the device is down when you switch alerting off, its site can leave alarm, and that sends a "recovered" message. Switch it back on and the device counts again.

### Down within seconds: an alert transport

Without it, a device going down shows after LibreNMS has polled it **and** the app's next LibreNMS read (every 5 minutes by default). With it, LibreNMS tells the app right away.

1. Make an API token in the app with the `operator` role:
   `docker compose exec api python -m app token create librenms --role operator`
2. In LibreNMS: **Alerts → Alert Transports → Create**, type **API**:
   - API Method: `POST`
   - API URL: `https://<nautobot-maps>/api/v1/librenms/alert`
   - Headers: `Authorization=Bearer nmt_...` (the token)
   - Body: `{"device_id": "{{ $device_id }}"}`
3. Attach the transport to your device-down rules (e.g. "Devices up/down"), with **recovery** alerts on.
4. Press **Test** on the transport. The app answers `{"updated": true, ...}`.

All the details: [LibreNMS alert pushes](configuration.md#near-real-time-status-librenms-alert-pushes).

## Wireless controllers (optional)

Cisco Catalyst Center and HPE Aruba New Central are not set up in Nautobot: they are listed in a file for the app, with read-only accounts. In Nautobot, give each access point its **Serial** and an access point **Role** (above). The setup: [wireless controllers](sources.md#wireless-controllers-access-points).

## When does a change show up?

- **Nautobot:** at the next sync, every 5 minutes by default (`INVENTORY_SYNC_INTERVAL_SECONDS`), and in full once a day.
- **LibreNMS:** at its next read, or within seconds with an alert transport.
- **Whether it is working:** the top bar is amber when a sync is late and red when Nautobot or LibreNMS can't be reached; its menu says which.
