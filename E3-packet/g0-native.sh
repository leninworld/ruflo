#!/bin/bash
# Tiny offline native compilation proves compiler/header/toolchain availability.
set -euo pipefail
command -v python3
command -v make
command -v g++
test -f /usr/local/include/node/node_api.h
cat > /work/g0-native.cc <<'CXX'
#include <node_api.h>
napi_value Init(napi_env env,napi_value exports){napi_value v;napi_create_int32(env,4876,&v);napi_set_named_property(env,exports,"probe",v);return exports;}
NAPI_MODULE(NODE_GYP_MODULE_NAME,Init)
CXX
g++ -DNODE_GYP_MODULE_NAME=g0 -shared -fPIC -I/usr/local/include/node /work/g0-native.cc -o /work/g0-native.node
node -e 'require("node:assert/strict").equal(require("/work/g0-native.node").probe,4876)'
python3 --version
make --version
g++ --version
