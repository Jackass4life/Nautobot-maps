# Data contract

<!-- Generated from nautobot_maps/contract.py: python -m nautobot_maps contract-docs > docs/data-contract.md -->

What Nautobot Maps reads from Nautobot and LibreNMS, which fields it uses, their types, and what each becomes in its own tables. Every sync checks the records it fetched against this: a **required** field that is missing, or any field with another type, is logged once per sync ("Data contract: nautobot dcim/devices/ does not match in 340 records: primary_ip4 is string, expected object or null (12)"), counted in `/metrics` (`nautobot_maps_contract_mismatches{source,endpoint,field}`) and shown by `GET /api/contract`. A mismatch is a warning: the sync goes on with what it can use.

After a Nautobot or LibreNMS upgrade, look there first. CI checks this contract against a real Nautobot on every change.

Types are JSON types. A nested field (`status.id`) is checked only when its parent is an object. Nested objects can be *brief* (Nautobot 3.x at depth 0: only `id` and `url`); the name then comes from the lookup endpoints at the end.

## Nautobot

### `dcim/locations/`

Map markers, the alert board's rows and their hierarchy.

- Request: all pages; incremental with last_updated__gte
- Stored in: `nautobot_location_cache`

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | id | — |
| `name` | string | yes | name | "Unknown" when empty |
| `display` | string or null |  | — | a parent's name when name is empty |
| `slug` | string or null |  | slug | — |
| `status` | object, string or null | yes | status | label, name or display; else extras/statuses by id |
| `status.id` | string |  | — | — |
| `status.label` | string or null |  | — | — |
| `status.name` | string or null |  | — | — |
| `status.display` | string or null |  | — | — |
| `location_type` | object, string or null | yes | location_type | name or display; else dcim/location-types by id |
| `location_type.id` | string |  | — | — |
| `location_type.name` | string or null |  | — | — |
| `location_type.display` | string or null |  | — | — |
| `parent` | object or null | yes | parent, parent_id | parent_id = parent.id; parent = its name, or the name of that location |
| `parent.id` | string |  | — | — |
| `parent.name` | string or null |  | — | — |
| `parent.display` | string or null |  | — | — |
| `tenant` | object or null | yes | tenant, tenant_id, tenant_group | name or display; else tenancy/tenants by id; tenant_group from the tenant |
| `tenant.id` | string |  | — | — |
| `tenant.name` | string or null |  | — | — |
| `tenant.display` | string or null |  | — | — |
| `latitude` | number, string or null | yes | latitude | float; a location without both coordinates has no map marker |
| `longitude` | number, string or null | yes | longitude | float |
| `physical_address` | string or null |  | physical_address | trimmed |
| `country` | object, string or null |  | country | name, display or label; else country_name; else the last part of physical_address |
| `country.name` | string or null |  | — | — |
| `country.display` | string or null |  | — | — |
| `country.label` | string or null |  | — | — |
| `country_name` | string or null |  | country | — |
| `description` | string or null |  | description | — |
| `facility` | string or null |  | facility | — |
| `time_zone` | string or null |  | time_zone | — |
| `asn` | integer or null |  | asn | — |
| `tags` | array or null |  | tags | each tag's name or display; else extras/tags by id |
| `url` | string or null |  | url | — |
| `last_updated` | string | yes | last_updated | the incremental sync's watermark (last_updated__gte) |

### `dcim/devices/`

The devices a site's alert level is computed from.

- Request: all pages, depth=1; incremental with last_updated__gte
- Stored in: `nautobot_device_cache`

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | id | — |
| `name` | string or null | yes | name | "Unknown" when empty |
| `status` | object, string or null | yes | status | label, name or display; else extras/statuses by id; decides up or down |
| `status.id` | string |  | — | — |
| `status.label` | string or null |  | — | — |
| `status.name` | string or null |  | — | — |
| `status.display` | string or null |  | — | — |
| `role` | object, string or null | yes | role | name or display; else extras/roles by id; decides criticality |
| `role.id` | string |  | — | — |
| `role.name` | string or null |  | — | — |
| `role.display` | string or null |  | — | — |
| `location` | object or null | yes | location_id | location.id |
| `location.id` | string | yes | — | — |
| `primary_ip4` | object, string or null | yes | primary_ip | host, address, display or name of primary_ip4, else primary_ip6, else primary_ip; a device without one is not monitored |
| `primary_ip4.host` | string or null |  | — | — |
| `primary_ip4.address` | string or null |  | — | — |
| `primary_ip4.display` | string or null |  | — | — |
| `primary_ip4.name` | string or null |  | — | — |
| `primary_ip6` | object, string or null |  | — | — |
| `primary_ip6.host` | string or null |  | — | — |
| `primary_ip6.address` | string or null |  | — | — |
| `primary_ip6.display` | string or null |  | — | — |
| `primary_ip6.name` | string or null |  | — | — |
| `primary_ip` | object, string or null |  | — | older Nautobot |
| `primary_ip.host` | string or null |  | — | — |
| `primary_ip.address` | string or null |  | — | — |
| `primary_ip.display` | string or null |  | — | — |
| `primary_ip.name` | string or null |  | — | — |
| `device_type` | object or null | yes | device_type, manufacturer | model or display, manufacturer name; else dcim/device-types and dcim/manufacturers by id |
| `device_type.id` | string |  | — | — |
| `device_type.model` | string or null |  | — | — |
| `device_type.display` | string or null |  | — | — |
| `device_type.manufacturer` | object or null |  | — | — |
| `device_type.manufacturer.id` | string |  | — | — |
| `device_type.manufacturer.name` | string or null |  | — | — |
| `device_type.manufacturer.display` | string or null |  | — | — |
| `platform` | object, string or null |  | platform | name or display |
| `platform.name` | string or null |  | — | — |
| `platform.display` | string or null |  | — | — |
| `tenant` | object or null |  | tenant | name or display; else tenancy/tenants by id |
| `tenant.id` | string |  | — | — |
| `tenant.name` | string or null |  | — | — |
| `tenant.display` | string or null |  | — | — |
| `serial` | string or null |  | serial | — |
| `last_updated` | string | yes | last_updated | the incremental sync's watermark |

### `tenancy/tenants/`

Tenant names, descriptions (tooltip) and tenant groups.

- Request: all pages
- Stored in: `nautobot_tenant_cache`

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | tenant_id | — |
| `name` | string | yes | name | name, else display (label, slug in lookups) |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |
| `description` | string or null |  | description | trimmed |
| `tenant_group` | object or null |  | tenant_group (of a location) | name or display; else tenancy/tenant-groups by id |
| `tenant_group.id` | string |  | — | — |
| `tenant_group.name` | string or null |  | — | — |
| `tenant_group.display` | string or null |  | — | — |

### `extras/relationships/`

Which Relationships link locations and tenants.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | to read its associations |
| `source_type` | string | yes | — | kept when source and destination are "dcim.location" and "tenancy.tenant" |
| `destination_type` | string | yes | — | — |
| `key` | string or null |  | relationship | key, else slug; matched against SITE_TENANT_RELATIONSHIPS |
| `slug` | string or null |  | — | — |
| `label` | string or null |  | relationship | label, name or display, else key |
| `name` | string or null |  | — | — |
| `display` | string or null |  | — | — |

### `extras/relationship-associations/`

The tenants linked to each location (#238).

- Request: all pages, relationship=<id>
- Stored in: `nautobot_location_tenant_cache`

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `source_id` | string | yes | location_id or tenant_id | by the relationship's direction |
| `destination_id` | string | yes | tenant_id or location_id | — |

### `extras/statuses/`

Status names when a nested status is brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `name` | string or null |  | — | the first of name, display, label or slug that is set |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |

### `extras/roles/`

Device role names when a nested role is brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `name` | string or null |  | — | the first of name, display, label or slug that is set |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |

### `dcim/location-types/`

Location type names when nested ones are brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `name` | string or null |  | — | the first of name, display, label or slug that is set |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |

### `extras/tags/`

Tag names when nested tags are brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `name` | string or null |  | — | the first of name, display, label or slug that is set |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |

### `dcim/manufacturers/`

Manufacturer names when nested ones are brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `name` | string or null |  | — | the first of name, display, label or slug that is set |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |

### `tenancy/tenant-groups/`

Tenant group names when nested ones are brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `name` | string or null |  | — | the first of name, display, label or slug that is set |
| `display` | string or null |  | — | — |
| `label` | string or null |  | — | — |
| `slug` | string or null |  | — | — |

### `dcim/device-types/`

Model and manufacturer when a device's device_type is brief.

- Request: all pages

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `id` | string | yes | — | key of the lookup |
| `model` | string or null |  | device_type | model, else display |
| `display` | string or null |  | — | — |
| `manufacturer` | object or null |  | manufacturer | name or display; else dcim/manufacturers by id |
| `manufacturer.id` | string |  | — | — |
| `manufacturer.name` | string or null |  | — | — |
| `manufacturer.display` | string or null |  | — | — |

## LibreNMS

### `devices?type=all`

Up/down status of every LibreNMS device, matched to Nautobot devices by id map, hostname or IP.

- Request: GET /api/v0/devices?type=all, X-Auth-Token; the devices array
- Stored in: `librenms_device_status`

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `device_id` | integer | yes | device_id | — |
| `hostname` | string | yes | hostname | matched to the Nautobot device name (short name, case-insensitive) or, when an IP, to its primary IP |
| `status` | integer or boolean | yes | status | 1/true = up, 0/false = down; down marks an active Nautobot device offline |
| `status_reason` | string or null |  | status_reason | — |
| `overwrite_ip` | string or null |  | ip | the address LibreNMS polls: overwrite_ip, else ip; only IP literals |
| `ip` | string or null |  | ip | — |

### `devices/<id or hostname>`

One device after an alert push (#284).

- Request: GET /api/v0/devices/<id or hostname>; the first entry of devices; 404 = unknown device
- Stored in: `librenms_device_status (only devices already there)`

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `device_id` | integer | yes | device_id | — |
| `hostname` | string | yes | hostname | matched to the Nautobot device name (short name, case-insensitive) or, when an IP, to its primary IP |
| `status` | integer or boolean | yes | status | 1/true = up, 0/false = down; down marks an active Nautobot device offline |
| `status_reason` | string or null |  | status_reason | — |
| `overwrite_ip` | string or null |  | ip | the address LibreNMS polls: overwrite_ip, else ip; only IP literals |
| `ip` | string or null |  | ip | — |

### `POST /api/librenms/alert (inbound)`

An alert push from LibreNMS's API transport (#284); only says which device to refresh.

- Request: Authorization: Bearer <API token, operator role>
- Documented only (an inbound request; validated by the endpoint itself)
- The alert's own state is ignored: the status is read from the LibreNMS API.

| Field | Type | Required | Becomes | Rule |
|---|---|---|---|---|
| `device_id` | integer or string |  | — | LibreNMS device id; JSON, form or query string |
| `hostname` | string |  | — | when there is no device_id |

## Not covered

The location detail panel's live reads from Nautobot (dcim/locations/<id>/ for the ASN, ipam/asns/, circuits/circuit-terminations/, and dcim/devices/ when the cache is empty): shown as they come, not stored.
