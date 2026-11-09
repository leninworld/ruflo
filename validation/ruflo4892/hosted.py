"""One approved hosted full-validation run, no Mac runtime or cloud private data."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time

GIB=1024**3
P=Path(__file__).resolve().parent
PLAN=json.loads((P/'execution-plan.json').read_text())
assert PLAN['status']=='SEALED_FOR_ONE_RUN', 'UNAPPROVED_DRAFT'
assert hashlib.sha256((P/'execution-plan.json').read_bytes()).hexdigest()==os.environ['APPROVED_PLAN_SHA256']
PROFILE,VARIANT=sys.argv[1:]
assert PROFILE in PLAN['profiles']
assert VARIANT in ['baseline','candidate']
assert os.environ['GITHUB_REPOSITORY']=='leninworld/ruflo' and os.environ['RUNNER_ENVIRONMENT']=='github-hosted'
assert os.environ['RUNNER_OS']=='Linux' and platform.machine()=='x86_64'
assert os.environ['GITHUB_RUN_ATTEMPT']=='1' and os.environ['GITHUB_REF']=='refs/heads/validate-hierarchical-value-4892-i1'
assert os.environ['GITHUB_EVENT_NAME']=='push' and os.environ['GITHUB_SHA']==os.environ['APPROVED_DIAGNOSTIC_COMMIT']
# One origin from the exact new diagnostic commit; no per-phase clock reset.
ORIGIN=int(subprocess.run(['git','-C',str(P),'show','-s','--format=%ct','HEAD'],capture_output=True,text=True,check=True,timeout=10).stdout.strip())
assert -120<=time.time()-ORIGIN<PLAN['target_seconds']
END=ORIGIN+PLAN['absolute_seconds']
ROOT=Path(os.environ['RUNNER_TEMP'])/('ruflo4892-'+os.environ['GITHUB_RUN_ID']+'-'+PROFILE+'-'+VARIANT)
ROOT.mkdir(exist_ok=False);E=ROOT/'evidence';E.mkdir()
SOURCE=Path(os.environ['GITHUB_WORKSPACE'])/'upstream'
WORK=ROOT/'work';SCRATCH=ROOT/'scratch';OWNED=[]
NETWORK_WINDOWS={};NETWORK_CONSUMED=0

def capture(args, cap=20, cleanup=False):
    if not cleanup:
        assert time.time() < END
    return subprocess.run(args, capture_output=True, text=True, check=True,
                          timeout=cap if cleanup else min(cap, max(1, END - time.time()))).stdout.strip()


def gate():
    mem = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    values = {'epoch': time.time(), 'available_memory': int(mem['MemAvailable'].split()[0]) * 1024,
              'free_disk': shutil.disk_usage(ROOT).free, 'cpus': int(capture(['nproc'])),
              'retained_peak_bytes': PLAN['retained_peak_bytes']}
    (E / 'admission.json').write_text(json.dumps(values, indent=2))
    assert values['available_memory'] >= 14 * GIB, 'HOST_MEMORY_ADMISSION'
    assert values['free_disk'] >= PLAN['minimum_free_disk_bytes'], 'HOST_DISK_ADMISSION'
    assert values['cpus'] >= 4, 'HOST_CPU_ADMISSION'


def stop(cid):
    assert cid in OWNED
    subprocess.run(['docker', 'stop', '--time', '5', cid], capture_output=True, timeout=20, check=False)
    state = json.loads(capture(['docker', 'inspect', '--format', '{{json .State}}', cid], cleanup=True))
    with (E / 'stops.jsonl').open('a') as handle:
        handle.write(json.dumps({'container': cid, 'state': state}) + '\n')
    assert state['Running'] is False and not state['OOMKilled'], 'OWNED_STOP_UNCONFIRMED_OR_OOM'


def net_bytes(cid):
    raw=capture(['docker','exec',cid,'cat','/proc/net/dev'],cap=5)
    total=0
    for line in raw.splitlines()[2:]:
        name,fields=line.split(':',1)
        if name.strip()=='lo':continue
        values=fields.split();total+=int(values[0])+int(values[8])
    return total


def observe_network(cid):
    current=net_bytes(cid)
    start=NETWORK_WINDOWS.get(cid)
    usage=NETWORK_CONSUMED+(max(0,current-start) if start is not None else 0)
    assert usage <= PLAN['network_bytes_cap'], 'NETWORK_BYTE_CAP'
    return {'container_bytes':current,'cumulative_network_bytes':usage}


def network(cid, connected):
    global NETWORK_CONSUMED
    if not connected and cid in NETWORK_WINDOWS:
        measured=observe_network(cid)
        NETWORK_CONSUMED=measured['cumulative_network_bytes'];del NETWORK_WINDOWS[cid]
        with (E/'network-windows.jsonl').open('a') as log:log.write(json.dumps(measured)+'\n')
    capture(['docker', 'network', 'connect' if connected else 'disconnect', 'bridge', cid])
    networks = json.loads(capture(['docker', 'inspect', '--format', '{{json .NetworkSettings.Networks}}', cid]))
    assert set(networks) == ({'bridge'} if connected else set()), 'NETWORK_TRANSITION_FAILED'
    if connected:NETWORK_WINDOWS[cid]=net_bytes(cid)


def create(image, readonly=False):
    mounts = [('type=bind,src=' + str(P) + ',dst=/packet,readonly'),
              ('type=bind,src=' + str(SOURCE) + ',dst=/source,readonly'),
              ('type=bind,src=' + str(WORK) + ',dst=/work' + (',readonly' if readonly else '')),
              ('type=bind,src=' + str(SCRATCH) + ',dst=/scratch')]
    args = ['docker', 'create', '--platform', 'linux/amd64', '--read-only', '--user', '1000:1000',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--memory', str(12 * GIB), '--memory-swap', str(12 * GIB), '--cpus', '4',
            '--pids-limit', '1024', '--network', 'none' if readonly else 'bridge',
            '--log-driver', 'local', '--log-opt', 'max-size=1m', '--log-opt', 'max-file=1',
            '--log-opt', 'compress=false',
            '--tmpfs', '/tmp:rw,exec,nosuid,nodev,mode=1777,size=1073741824',
            '--env', 'PROFILE=' + PROFILE, '--env', 'VARIANT=' + VARIANT,
            '--env', 'ATTEMPT_DEADLINE=' + str(END)]
    for mount in mounts:
        args += ['--mount', mount]
    args += [image, 'sleep', '3600']
    cid = capture(args)
    assert len(cid) == 64 and all(c in '0123456789abcdef' for c in cid)
    OWNED.append(cid)
    capture(['docker', 'start', cid])
    return cid


def phase(cid, name):
    out, err = E / (name + '.stdout'), E / (name + '.stderr')
    begin = time.monotonic()
    last_sample = begin
    problem = None
    with out.open('xb') as stdout, err.open('xb') as stderr:
        child = subprocess.Popen(['docker', 'exec', cid, 'python3', '/packet/inside.py', name],
                                 stdout=stdout, stderr=stderr)
        try:
            while child.poll() is None:
                if time.time() >= END - 180 or time.monotonic() - begin >= PLAN['phase_caps'][name]:
                    raise TimeoutError('OWNED_PHASE_DEADLINE')
                if out.stat().st_size + err.stat().st_size > 32 * 1024 ** 2:
                    raise RuntimeError('HOST_LOG_CAP')
                if shutil.disk_usage(ROOT).free < 4 * GIB:
                    raise RuntimeError('DISK_RESERVE')
                if time.monotonic() - last_sample >= 10:
                    stats = capture(['docker', 'stats', '--no-stream', '--format', '{{json .}}', cid], cap=15)
                    with (E / 'stats.jsonl').open('a') as log:
                        log.write(json.dumps({'phase': name, 'epoch': time.time(), 'stats': json.loads(stats), 'network':observe_network(cid), 'pids_current':capture(['docker','exec',cid,'cat','/sys/fs/cgroup/pids.current'],cap=5)}) + '\n')
                    measured = subprocess.run(['sudo', 'du', '-s', '--block-size=1', str(WORK)],
                                              capture_output=True, text=True, timeout=15)
                    if measured.returncode:
                        lines = measured.stderr.splitlines()
                        assert measured.returncode == 1 and lines and all(
                            'No such file or directory' in line for line in lines), measured.stderr
                        with (E / 'sampling-misses.jsonl').open('a') as log:
                            log.write(json.dumps({'phase': name, 'stderr': measured.stderr[:4096]}) + '\n')
                    else:
                        assert int(measured.stdout.split()[0]) <= 16 * GIB, 'WORK_DISK_CAP'
                    last_sample = time.monotonic()
                time.sleep(.25)
        except BaseException as error:
            problem = repr(error)
            stop(cid)
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
    peak = capture(['docker', 'exec', cid, 'cat', '/sys/fs/cgroup/memory.peak']) if problem is None else None
    receipt = {'phase': name, 'exit': child.returncode, 'stop': problem,
               'seconds': time.monotonic() - begin, 'container_memory_peak_bytes': peak,
               'disk_free': shutil.disk_usage(ROOT).free}
    (E / (name + '-receipt.json')).write_text(json.dumps(receipt, indent=2))
    assert problem is None and child.returncode == 0, receipt


def main():
    gate()
    assert capture(['git','-C',str(SOURCE),'rev-parse','HEAD'])==PLAN['source_pin']
    assert not capture(['git','-C',str(SOURCE),'status','--porcelain','--untracked-files=all'])
    source_bytes=sum(f.stat().st_size for f in SOURCE.rglob('*') if f.is_file() and not f.is_symlink())
    assert source_bytes<=512*1024**2, 'SOURCE_CHECKOUT_BYTE_BOUND'
    (E/'source-disk-byte-bound.json').write_text(json.dumps({'bytes':source_bytes,'bound':512*1024**2})+'\n')
    names=capture(['git','-C',str(SOURCE),'config','--name-only','--list'])
    assert not any('extraheader' in key.lower() or key.lower().startswith('credential.') for key in names.splitlines())
    raw=capture(['git','-C',str(P),'cat-file','-p','HEAD']);header=raw.split('\n\n',1)[0]
    assert not any(line.startswith('parent ') for line in header.splitlines()),'DIAGNOSTIC_MUST_BE_ORPHAN'
    (E/'raw-commit-header.txt').write_text(header+'\n')
    for row in PLAN['packet_files']:
        assert hashlib.sha256((P/row['path']).read_bytes()).hexdigest()==row['sha256'],row['path']
    for directory in [WORK,SCRATCH]:directory.mkdir()
    for name in ['home','tmp','cache','evidence']:(WORK/name).mkdir()
    (SCRATCH/'evidence').mkdir()
    capture(['sudo','chown','-R','1000:1000',str(WORK),str(SCRATCH)])
    node=PLAN['profiles'][PROFILE]['node']
    image=PLAN['images'][node]
    capture(['docker','pull','--platform','linux/amd64',image],cap=240)
    global NETWORK_CONSUMED
    image_size=int(capture(['docker','image','inspect','--format','{{.Size}}',image]))
    assert image_size+2*GIB<PLAN['network_bytes_cap'];NETWORK_CONSUMED=image_size+2*GIB
    (E/'image-size-network-upper-bound.json').write_text(json.dumps({'image_uncompressed_bytes':image_size,'checkout_action_artifact_reserve_bytes':2*GIB,'qualification':'Conservative image layer bound plus measured container RX+TX and2GiB bounded checkout/actions/artifact reserve. Source and evidence bounds enforced separately.'}))
    cid=create(image);NETWORK_WINDOWS[cid]=net_bytes(cid);network(cid,False);phase(cid,'prepare')
    network(cid,True);phase(cid,'install');network(cid,False)
    phase(cid,'early');phase(cid,'test')
    if PROFILE=='shipping22':
        network(cid,True);phase(cid,'shipping-warm');network(cid,False);phase(cid,'shipping-disconnected-proof')
    stop(cid)
    (E/'host-terminal.json').write_text(json.dumps({'state':'COMPLETE','profile':PROFILE,'variant':VARIANT,'origin':ORIGIN,'elapsed':time.time()-ORIGIN})+'\n')


if __name__=='__main__':
    signal.signal(signal.SIGTERM,lambda signum,frame:(_ for _ in ()).throw(KeyboardInterrupt(signum)))
    try:main()
    finally:
        errors=[]
        for cid in OWNED:
            try:
                stop(cid);(E/(cid[:12]+'-container.json')).write_text(capture(['docker','inspect',cid],cleanup=True))
            except Exception as error:errors.append({'container':cid,'error':repr(error)})
        # Preserve every nested success/failure receipt before indexing.
        for folder in [WORK/'evidence',SCRATCH/'evidence']:
            if folder.exists():shutil.copytree(folder,E/folder.parent.name,symlinks=False)
        index=[{'name':str(path.relative_to(E)),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in sorted(E.rglob('*')) if path.is_file()]
        assert sum(row['bytes'] for row in index)<=512*1024**2, 'EVIDENCE_BYTE_BOUND'
        (E/'evidence-index.json').write_text(json.dumps(index,indent=2)+'\n')
        (E/'cleanup-result.json').write_text(json.dumps({'errors':errors})+'\n')
        assert not errors,'STOP_OR_OOM_UNCONFIRMED'
