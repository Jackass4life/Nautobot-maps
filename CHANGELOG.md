# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Location detail returned 502 on Nautobot 3.x without the BGP Models plugin: a 404 from `ipam/asns/` now falls back to the location's own `asn` field, while other upstream errors still fail (#126)

### Added
- Initial public release
- Interactive OpenStreetMap visualization of Nautobot locations
- Color-coded markers by location status (Active, Planned, Other)
- Filtering by status, location type, parent, tenant, and tenant group
- Click-to-view location details with devices, ASNs, and tenant info
- Address and GPS coordinate search with 5 km proximity matching
- Grid-based marker clustering for large deployments
- Co-located site grouping with tabbed popups
- Server-side caching to reduce Nautobot API load
- Docker and Docker Compose deployment support
- Demo mode with mock data
- Support for Nautobot v2.x and v3.x APIs
- Comprehensive test suite (unit, integration, and live tests)
