#!/bin/bash
set -o xtrace
./full-service \
  --wallet-db /data/wallet.db \
  --ledger-db /data/ledger-db/ \
  --peer mc://node1.prod.mobilecoinww.com/ \
  --peer mc://node2.prod.mobilecoinww.com/ \
  --tx-source-url https://ledger.mobilecoinww.com/node1.prod.mobilecoinww.com/ \
  --tx-source-url https://ledger.mobilecoinww.com/node2.prod.mobilecoinww.com/ \
  --listen-host 0.0.0.0
