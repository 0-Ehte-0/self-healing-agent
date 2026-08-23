-- Create additional databases if they do not exist
SELECT 'CREATE DATABASE demo'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'demo')\gexec

-- Connect to control_plane and enable uuid-ossp extension
\c control_plane;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Connect to demo and enable uuid-ossp extension
\c demo;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";