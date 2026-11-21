-- Delivery channels (owner B, spec §6, §8). `adapter` picks the class from services/delivery/channels;
-- `config` is passed to its constructor. Swapping in a real provider = new adapter class + UPDATE of the row.
CREATE TABLE channels (
    name    text PRIMARY KEY,
    adapter text    NOT NULL,
    config  jsonb   NOT NULL DEFAULT '{}',
    enabled boolean NOT NULL DEFAULT true
);

-- Demo defaults: hosts are docker-compose service names.
INSERT INTO channels (name, adapter, config) VALUES
    ('ui',    'websocket',  '{}'),
    ('email', 'smtp',       '{"host": "mailpit", "port": 1025, "from": "alerts@alerting.local"}'),
    ('sms',   'fake_sms',   '{"url": "http://fake-gateway:8090/sms"}'),
    ('phone', 'fake_phone', '{"url": "http://fake-gateway:8090/calls"}');
