"""Tiny owned pre-dependency check of the real container network transitions.

No external address, DNS, dependency import or project code is contacted.
"""
import json,re,socket,sys
from pathlib import Path
mode,stage=sys.argv[1:];assert mode in ['online','offline'] and re.fullmatch('[a-z-]+',stage)
interfaces=sorted(p.name for p in Path('/sys/class/net').iterdir())
routes=[]
for line in Path('/proc/net/route').read_text().splitlines()[1:]:
 columns=line.split()
 if len(columns)>=8 and columns[1]=='00000000' and columns[7]=='00000000' and int(columns[3],16)&1:routes.append({'interface':columns[0],'gateway_hex':columns[2]})
if mode=='online':assert any(i!='lo' for i in interfaces) and routes,'BRIDGE_INTERFACE_OR_DEFAULT_ROUTE_MISSING'
else:
 assert 'lo' in interfaces,'OFFLINE_LOOPBACK_MISSING'
 fallback_tunnels={'tunl0','gre0','gretap0','erspan0','sit0','ip6tnl0','ip6gre0','ip_vti0','ip6_vti0'}
 for interface in interfaces:
  if interface=='lo':continue
  assert interface in fallback_tunnels and (Path('/sys/class/net')/interface/'operstate').read_text().strip()=='down','OFFLINE_INTERFACE_LEAK'
 assert not routes,'OFFLINE_IPV4_DEFAULT_ROUTE_LEAK'
 ipv6=Path('/proc/net/ipv6_route')
 if ipv6.exists():
  for line in ipv6.read_text().splitlines():
   columns=line.split()
   assert not (len(columns)>=10 and columns[0]=='0'*32 and columns[1]=='00' and columns[-1]!='lo'),'OFFLINE_IPV6_DEFAULT_ROUTE_LEAK'
with socket.socket() as listener:
 listener.settimeout(1);listener.bind(('127.0.0.1',0));listener.listen(1)
 with socket.create_connection(listener.getsockname(),timeout=1) as client:
  peer,_=listener.accept()
  with peer:
   peer.settimeout(1);client.sendall(b'E3');assert peer.recv(2)==b'E3';peer.sendall(b'OK');assert client.recv(2)==b'OK'
result={'stage':stage,'expected_mode':mode,'interfaces':interfaces,'non_loopback_interfaces':{i:(Path('/sys/class/net')/i/'operstate').read_text().strip() for i in interfaces if i!='lo'},'default_routes':routes,'real_loopback_roundtrip':True,'external_packets_requested':False}
evidence=Path('/work/evidence');evidence.mkdir(exist_ok=True);(evidence/('network-transition-'+stage+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
