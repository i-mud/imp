"""Frozen Linux server entry point: the imp-server multicall runtime.

Role imports are explicit so PyInstaller bundles every role. The TinyFugue roles live
here, not in the relay package, which must not depend on the adapter packages.
"""

from imp_tf.action_consumer import main as action_consumer
from imp_tf.feed import main as feed
from imp_tf.feed_watch import main as feed_watch

from imp_relay.__main__ import main as relay
from imp_relay.direct_edge import main as direct_edge
from imp_relay.direct_wss import main as direct_wss
from imp_relay.gateway_main import main as gateway
from imp_relay.public_verifier import main as public_verify
from imp_relay.server_runtime import main

ROLES = {
    "relay": relay,
    "gateway": gateway,
    "feed": feed,
    "feed-watch": feed_watch,
    "action-consumer": action_consumer,
    "direct-wss": direct_wss,
    "direct-edge": direct_edge,
    "public-verify": public_verify,
}

if __name__ == "__main__":
    main(ROLES)
