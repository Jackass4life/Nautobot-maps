# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
