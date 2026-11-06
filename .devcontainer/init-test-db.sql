-- Separate database for pytest, so tests never touch the app's data.
CREATE DATABASE nautobot_maps_test;
-- api/'s tests (#588), apart from the current app's: they reset their database.
CREATE DATABASE fastapi_nuxt_test;
