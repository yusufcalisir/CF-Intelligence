import { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import * as THREE from 'three';

// SVG Icons
const CodeIcon = () => (
  <svg className="w-5 h-5 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <polyline points="16 18 22 12 16 6" />
    <polyline points="8 6 2 12 8 18" />
  </svg>
);

const ArrowRightIcon = () => (
  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="5" y1="12" x2="19" y2="12" />
    <polyline points="12 5 19 12 12 19" />
  </svg>
);

const LockIcon = () => (
  <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
    <path d="M7 11V7a5 5 0 0 1 10 0v4" />
  </svg>
);

const MenuIcon = () => (
  <svg className="w-6 h-6 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="3" y1="12" x2="21" y2="12" />
    <line x1="3" y1="6" x2="21" y2="6" />
    <line x1="3" y1="18" x2="21" y2="18" />
  </svg>
);

const CloseIcon = () => (
  <svg className="w-6 h-6 text-slate-200" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </svg>
);

// Synchronized Premium Vector SVG Logo Mark (Identical to public/favicon.svg)
const CfiLogoMark = ({ className = "w-9 h-9" }: { className?: string }) => (
  <svg className={className} viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <linearGradient id="cfi-border-grad-component" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#6366f1" />
        <stop offset="50%" stopColor="#a855f7" />
        <stop offset="100%" stopColor="#10b981" />
      </linearGradient>

      <linearGradient id="cfi-shield-grad-component" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stopColor="#1e1b4b" stopOpacity="0.9" />
        <stop offset="100%" stopColor="#020617" stopOpacity="0.95" />
      </linearGradient>

      <filter id="cfi-glow-component" x="-20%" y="-20%" width="140%" height="140%">
        <feGaussianBlur stdDeviation="3" result="blur" />
        <feComposite in="SourceGraphic" in2="blur" operator="over" />
      </filter>
    </defs>

    <rect x="4" y="4" width="112" height="112" rx="28" fill="#030712" stroke="url(#cfi-border-grad-component)" strokeWidth="3" />

    <g stroke="#334155" strokeWidth="1" strokeDasharray="2 3" opacity="0.35">
      <line x1="60" y1="18" x2="60" y2="102" />
      <line x1="18" y1="60" x2="102" y2="60" />
    </g>

    <path d="M 60,22 C 78,22 92,26 94,36 C 94,62 82,88 60,98 C 38,88 26,62 26,36 C 28,26 42,22 60,22 Z"
          fill="url(#cfi-shield-grad-component)"
          stroke="url(#cfi-border-grad-component)"
          strokeWidth="3.5"
          strokeLinejoin="round"
          filter="url(#cfi-glow-component)" />

    <g stroke="#818cf8" strokeWidth="2" opacity="0.75">
      <line x1="60" y1="36" x2="38" y2="54" />
      <line x1="60" y1="36" x2="82" y2="54" />
      <line x1="38" y1="54" x2="60" y2="76" />
      <line x1="82" y1="54" x2="60" y2="76" />
      <line x1="60" y1="36" x2="60" y2="56" />
      <line x1="38" y1="54" x2="60" y2="56" />
      <line x1="82" y1="54" x2="60" y2="56" />
      <line x1="60" y1="76" x2="60" y2="56" />
    </g>

    <circle cx="60" cy="36" r="4.5" fill="#38bdf8" />
    <circle cx="38" cy="54" r="4.5" fill="#6366f1" />
    <circle cx="82" cy="54" r="4.5" fill="#ec4899" />
    <circle cx="60" cy="76" r="4.5" fill="#10b981" />

    <circle cx="60" cy="56" r="7.5" fill="#a855f7" stroke="#ffffff" strokeWidth="2" filter="url(#cfi-glow-component)" />
    <circle cx="60" cy="56" r="3" fill="#ffffff" />
  </svg>
);

// Real Global Bank Information Interface for Drawer
interface BankInfoDetail {
  id: string;
  name: string;
  ticker: string;
  location: string;
  hardware: string;
  ram: string;
  pytorch: string;
  latency: string;
  xmlLogs: string[];
}

const REAL_BANK_DETAILS: Record<string, BankInfoDetail> = {
  jpmorgan: {
    id: 'jpmorgan',
    name: 'JPMorgan Chase & Co.',
    ticker: 'NYSE: JPM',
    location: 'New York, US (Node #01)',
    hardware: 'NVIDIA A100 Tensor Core (80GB VRAM)',
    ram: '128 GB Host RAM',
    pytorch: '2.2.1+cu121',
    latency: '0.8 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'ISO 20022 intake parsed: 4,800 transactions/sec. Local PyTorch GNN embeddings generated.',
      'Differential Privacy noise injected: Gaussian(0, 0.05). Encrypted gradient uploaded to Enclave.',
    ],
  },
  hsbc: {
    id: 'hsbc',
    name: 'HSBC Holdings plc',
    ticker: 'LSE: HSBC',
    location: 'London, UK (Node #02)',
    hardware: 'NVIDIA H100 SXM (80GB VRAM)',
    ram: '64 GB Host RAM',
    pytorch: '2.2.1+cu121',
    latency: '1.4 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>HSBC-2026-8810</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="GBP">890000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'ISO 20022 intake parsed: 3,200 transactions/sec. Local PyTorch GNN embeddings generated.',
      'Paillier homomorphic ciphertext generated: [[W_hsbc]]. Ready for federated aggregation.',
    ],
  },
  deutsche: {
    id: 'deutsche',
    name: 'Deutsche Bank AG',
    ticker: 'XETRA: DBK',
    location: 'Frankfurt, DE (Node #03)',
    hardware: 'Intel Xeon Platinum Cluster (CPU Monolith)',
    ram: '32 GB Host RAM',
    pytorch: '2.1.2+cpu',
    latency: '2.9 ms',
    xmlLogs: [
      '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08"><FIToFICstmrCdtTrf><GrpHdr><MsgId>DBK-2026-7734</MsgId></GrpHdr><CdtTrfTxInf><IntrBkSttlmAmt Ccy="EUR">650000.00</IntrBkSttlmAmt></CdtTrfTxInf></FIToFICstmrCdtTrf></Document>',
      'Heterogeneous parameter negotiator applied: Batch size scaled to 32.',
      'CPU threadpool gradient accumulation steps = 2. Straggler delay quenched.',
    ],
  },
  sgx: {
    id: 'sgx',
    name: 'Intel SGX Hardware TEE Enclave',
    ticker: 'HARDWARE TEE',
    location: 'Consortium Secure Vault Node',
    hardware: 'Intel SGX Enclave v2 (Hardware Isolation)',
    ram: '256 GB Enclave Page Cache (EPC)',
    pytorch: 'C++ Native LibTorch Enclave Runtime',
    latency: '0.2 ms',
    xmlLogs: [
      'Intel SGX Attestation Verification: SUCCESS (Cryptographic Proof Verified).',
      'Homomorphic Sum Aggregation executed: [[W_global]] = Sum([[W_jpm]], [[W_hsbc]], [[W_db]]).',
      'Differential Privacy Gaussian noise injected (ε=0.50, δ=1e-5). Model parameters published.',
    ],
  },
};

// Graph Node Telemetry Inspector Interface
interface GraphNodeDetail {
  id: string;
  name: string;
  bank: string;
  riskScore: number;
  velocity: string;
  anomalyIndex: string;
  status: string;
  description: string;
}

const GRAPH_NODES_DATA: Record<string, GraphNodeDetail> = {
  jpm: { id: 'JPM-ACCT-01', name: 'JPMorgan Source Account', bank: 'JPMorgan Chase (US)', riskScore: 0.12, velocity: '14 tx/min', anomalyIndex: 'Low (0.04)', status: 'CLEARED', description: 'Legitimate corporate originator initiating international settlement.' },
  shell_a: { id: 'SHELL-CORP-A', name: 'Shell Corp Alpha', bank: 'Santander UK', riskScore: 0.94, velocity: '340 tx/min', anomalyIndex: 'CRITICAL (0.92)', status: 'FLAGGED', description: 'Rapid layering shell account splitting funds into sub-threshold amounts.' },
  smurf_1: { id: 'SMURF-ACCT-1', name: 'Money Mule Smurf 1', bank: 'Barclays UK', riskScore: 0.96, velocity: '120 tx/min', anomalyIndex: 'CRITICAL (0.95)', status: 'FLAGGED', description: 'Intermediary mule account forwarding funds across borders.' },
  hsbc: { id: 'HSBC-ACCT-02', name: 'HSBC Destination Account', bank: 'HSBC Holdings (UK)', riskScore: 0.15, velocity: '8 tx/min', anomalyIndex: 'Low (0.05)', status: 'CLEARED', description: 'Target commercial recipient account.' },
  shell_b: { id: 'SHELL-CORP-B', name: 'Shell Corp Beta', bank: 'BNP Paribas', riskScore: 0.92, velocity: '280 tx/min', anomalyIndex: 'HIGH (0.88)', status: 'FLAGGED', description: 'Secondary layering entity attempting to obfuscate audit trails.' },
  smurf_2: { id: 'SMURF-ACCT-2', name: 'Money Mule Smurf 2', bank: 'Credit Agricole', riskScore: 0.95, velocity: '160 tx/min', anomalyIndex: 'CRITICAL (0.94)', status: 'FLAGGED', description: 'Intermediary mule account fanning out transactions.' },
  db: { id: 'DB-RELAY-03', name: 'Deutsche Bank Relay Node', bank: 'Deutsche Bank (DE)', riskScore: 0.18, velocity: '45 tx/min', anomalyIndex: 'Normal (0.12)', status: 'CLEARED', description: 'High-volume euro clearing node.' },
  offramp: { id: 'CRYPTO-OFFRAMP', name: 'Crypto Exchange Offramp', bank: 'Unregulated Offramp', riskScore: 0.98, velocity: '890 tx/min', anomalyIndex: 'SEVERE (0.99)', status: 'ISOLATED', description: 'Ultimate illicit exit node converting fiat to unhosted wallets.' },
};

// ── REFERENCE-ACCURATE 3D WEBGL GRAPHICS SCENE COMPONENT (THREE.JS) ─────────────
function Real3DBankScene({
  onSelectBank,
}: {
  onSelectBank: (bank: BankInfoDetail) => void;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;

    const width = container.clientWidth || 600;
    const height = container.clientHeight || 480;

    // 1. Three.js Scene, Camera, Renderer
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2('#020617', 0.035);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 1000);
    camera.position.set(0, 11, 13);
    camera.lookAt(0, 0, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    container.appendChild(renderer.domElement);

    // 2. Lighting Setup
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.7);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0x818cf8, 2.5);
    dirLight.position.set(5, 15, 8);
    dirLight.castShadow = true;
    scene.add(dirLight);

    const bluePoint = new THREE.PointLight(0x38bdf8, 4, 25);
    bluePoint.position.set(-4.5, 4, -3.5);
    scene.add(bluePoint);

    const magentaPoint = new THREE.PointLight(0xec4899, 4, 25);
    magentaPoint.position.set(4.5, 4, -3.5);
    scene.add(magentaPoint);

    const purplePoint = new THREE.PointLight(0xa855f7, 4, 25);
    purplePoint.position.set(-4.5, 4, 3.5);
    scene.add(purplePoint);

    const cyanPoint = new THREE.PointLight(0x06b6d4, 4, 25);
    cyanPoint.position.set(4.5, 4, 3.5);
    scene.add(cyanPoint);

    // 3. Central 3D Processor Microchip Mesh Group
    const chipGroup = new THREE.Group();

    const chipGeo = new THREE.BoxGeometry(2.6, 0.4, 2.6);
    const chipMat = new THREE.MeshStandardMaterial({
      color: 0x0f172a,
      metalness: 0.95,
      roughness: 0.15,
    });
    const chipMesh = new THREE.Mesh(chipGeo, chipMat);
    chipMesh.castShadow = true;
    chipMesh.receiveShadow = true;
    chipGroup.add(chipMesh);

    const coreGeo = new THREE.CylinderGeometry(0.75, 0.75, 0.45, 32);
    const coreMat = new THREE.MeshStandardMaterial({
      color: 0x38bdf8,
      emissive: 0x0284c7,
      emissiveIntensity: 0.9,
      metalness: 0.6,
      roughness: 0.1,
    });
    const coreMesh = new THREE.Mesh(coreGeo, coreMat);
    chipGroup.add(coreMesh);

    // 32 Golden Pins around Microchip
    const pinGeo = new THREE.BoxGeometry(0.12, 0.08, 0.45);
    const pinMat = new THREE.MeshStandardMaterial({ color: 0xd97706, metalness: 0.95, roughness: 0.1 });
    for (let i = -1; i <= 1; i += 0.3) {
      const p1 = new THREE.Mesh(pinGeo, pinMat);
      p1.position.set(i * 1.8, 0, 1.45);
      chipGroup.add(p1);

      const p2 = new THREE.Mesh(pinGeo, pinMat);
      p2.position.set(i * 1.8, 0, -1.45);
      chipGroup.add(p2);

      const p3 = new THREE.Mesh(pinGeo, pinMat);
      p3.rotation.y = Math.PI / 2;
      p3.position.set(1.45, 0, i * 1.8);
      chipGroup.add(p3);

      const p4 = new THREE.Mesh(pinGeo, pinMat);
      p4.rotation.y = Math.PI / 2;
      p4.position.set(-1.45, 0, i * 1.8);
      chipGroup.add(p4);
    }

    const torusGeo = new THREE.TorusGeometry(2.0, 0.03, 16, 100);
    const torusMat = new THREE.MeshBasicMaterial({ color: 0x38bdf8 });
    const torusMesh = new THREE.Mesh(torusGeo, torusMat);
    torusMesh.rotation.x = Math.PI / 2;
    chipGroup.add(torusMesh);

    scene.add(chipGroup);

    // 4. Glass-Enclosed Cubic Pedestals & Custom 3D Bank Architecture
    const createBankPedestalMesh = (
      color: number,
      pos: [number, number, number],
      bankKey: string,
      type: 'server_rack' | 'server_cluster' | 'vault' | 'bank_building'
    ) => {
      const group = new THREE.Group();
      group.position.set(...pos);
      group.userData = { bankKey };

      const platGeo = new THREE.BoxGeometry(2.8, 0.22, 2.8);
      const platMat = new THREE.MeshPhysicalMaterial({
        color: 0x0f172a,
        metalness: 0.9,
        roughness: 0.2,
      });
      const platMesh = new THREE.Mesh(platGeo, platMat);
      platMesh.receiveShadow = true;
      group.add(platMesh);

      const rimGeo = new THREE.BoxGeometry(2.86, 0.05, 2.86);
      const rimMat = new THREE.MeshBasicMaterial({ color });
      const rimMesh = new THREE.Mesh(rimGeo, rimMat);
      rimMesh.position.y = 0.12;
      group.add(rimMesh);

      const glassGeo = new THREE.BoxGeometry(2.4, 2.4, 2.4);
      const glassMat = new THREE.MeshPhysicalMaterial({
        color,
        transmission: 0.85,
        opacity: 0.45,
        transparent: true,
        roughness: 0.1,
        ior: 1.4,
      });
      const glassMesh = new THREE.Mesh(glassGeo, glassMat);
      glassMesh.position.y = 1.35;
      group.add(glassMesh);

      if (type === 'server_rack' || type === 'server_cluster') {
        const rackGeo = new THREE.BoxGeometry(1.5, 1.8, 1.3);
        const rackMat = new THREE.MeshStandardMaterial({
          color: 0x1e293b,
          metalness: 0.9,
          roughness: 0.2,
        });
        const rackMesh = new THREE.Mesh(rackGeo, rackMat);
        rackMesh.position.y = 1.15;
        rackMesh.castShadow = true;
        group.add(rackMesh);

        const ledGeo = new THREE.BoxGeometry(1.3, 0.08, 0.04);
        const ledMat = new THREE.MeshBasicMaterial({ color });
        for (let y = 0.4; y <= 1.6; y += 0.3) {
          const led = new THREE.Mesh(ledGeo, ledMat);
          led.position.set(0, y, 0.67);
          group.add(led);
        }
      } else if (type === 'bank_building') {
        const bldgBaseGeo = new THREE.BoxGeometry(1.7, 0.8, 1.5);
        const bldgMat = new THREE.MeshStandardMaterial({ color: 0x1e293b, metalness: 0.8 });
        const bldgBase = new THREE.Mesh(bldgBaseGeo, bldgMat);
        bldgBase.position.y = 0.6;
        group.add(bldgBase);

        const colGeo = new THREE.CylinderGeometry(0.08, 0.08, 0.9, 16);
        const colMat = new THREE.MeshStandardMaterial({ color: 0x38bdf8, metalness: 0.9 });
        [-0.6, -0.2, 0.2, 0.6].forEach((x) => {
          const col = new THREE.Mesh(colGeo, colMat);
          col.position.set(x, 1.35, 0.6);
          group.add(col);
        });

        const roofGeo = new THREE.BoxGeometry(1.8, 0.25, 1.6);
        const roofMesh = new THREE.Mesh(roofGeo, bldgMat);
        roofMesh.position.y = 1.85;
        group.add(roofMesh);
      } else {
        const vaultGeo = new THREE.BoxGeometry(1.6, 1.6, 1.6);
        const vaultMat = new THREE.MeshStandardMaterial({ color: 0xa855f7, metalness: 0.95, roughness: 0.1 });
        const vaultMesh = new THREE.Mesh(vaultGeo, vaultMat);
        vaultMesh.position.y = 1.15;
        group.add(vaultMesh);
      }

      scene.add(group);
      return group;
    };

    const jpmMesh = createBankPedestalMesh(0x38bdf8, [-4.5, 0, -3.5], 'jpmorgan', 'server_rack');
    const hsbcMesh = createBankPedestalMesh(0xec4899, [4.5, 0, -3.5], 'hsbc', 'server_cluster');
    const sgxMesh = createBankPedestalMesh(0xa855f7, [-4.5, 0, 3.5], 'sgx', 'vault');
    const dbMesh = createBankPedestalMesh(0x06b6d4, [4.5, 0, 3.5], 'deutsche', 'bank_building');

    const bankNodesList = [jpmMesh, hsbcMesh, sgxMesh, dbMesh];

    // 5. Curved Dual-Color Laser Lines & Animated 3D Arrow Cones
    const createFiberWith3DArrows = (start: [number, number, number], end: [number, number, number], color: number) => {
      const curve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(...start),
        new THREE.Vector3((start[0] + end[0]) * 0.5, 1.9, (start[2] + end[2]) * 0.5),
        new THREE.Vector3(...end),
      ]);

      const tubeGeo = new THREE.TubeGeometry(curve, 64, 0.045, 8, false);
      const tubeMat = new THREE.MeshBasicMaterial({ color });
      const tubeMesh = new THREE.Mesh(tubeGeo, tubeMat);
      scene.add(tubeMesh);

      const arrowGeo = new THREE.ConeGeometry(0.18, 0.45, 12);
      const arrowMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
      const arrowMesh = new THREE.Mesh(arrowGeo, arrowMat);
      scene.add(arrowMesh);

      return { curve, arrowMesh };
    };

    const fibers = [
      createFiberWith3DArrows([-4.5, 1.3, -3.5], [0, 0.2, 0], 0x38bdf8),
      createFiberWith3DArrows([4.5, 1.3, -3.5], [0, 0.2, 0], 0xec4899),
      createFiberWith3DArrows([-4.5, 1.3, 3.5], [0, 0.2, 0], 0xa855f7),
      createFiberWith3DArrows([4.5, 1.3, 3.5], [0, 0.2, 0], 0x06b6d4),
    ];

    // 6. Interactive Mouse Orbit & Raycaster
    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let targetRotY = 0;
    let targetRotX = 0;

    const handleMouseMove = (e: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      const y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      mouse.x = x;
      mouse.y = y;

      targetRotY = x * 0.3;
      targetRotX = y * 0.15;
    };

    const handleClick = () => {
      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(scene.children, true);

      for (const hit of intersects) {
        let parent: THREE.Object3D | null = hit.object;
        while (parent && !parent.userData.bankKey) {
          parent = parent.parent;
        }
        if (parent && parent.userData.bankKey) {
          const key = parent.userData.bankKey;
          if (REAL_BANK_DETAILS[key]) {
            onSelectBank(REAL_BANK_DETAILS[key]);
          }
          break;
        }
      }
    };

    container.addEventListener('mousemove', handleMouseMove);
    container.addEventListener('click', handleClick);

    const handleResize = () => {
      if (!container) return;
      const w = container.clientWidth || 600;
      const h = container.clientHeight || 480;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', handleResize);

    // 7. Render Loop (60FPS)
    let animId: number;
    let clock = new THREE.Clock();

    const animate = () => {
      const elapsedTime = clock.getElapsedTime();

      scene.rotation.y += (targetRotY - scene.rotation.y) * 0.05;
      scene.rotation.x += (targetRotX - scene.rotation.x) * 0.05;

      chipGroup.rotation.y = elapsedTime * 0.45;
      torusMesh.rotation.z = elapsedTime * 0.9;
      coreMat.emissiveIntensity = 0.7 + Math.sin(elapsedTime * 4) * 0.3;

      fibers.forEach((f, idx) => {
        const t = (elapsedTime * 0.35 + idx * 0.25) % 1;
        const pt = f.curve.getPoint(t);
        const tangent = f.curve.getTangent(t);

        f.arrowMesh.position.copy(pt);
        f.arrowMesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tangent);
      });

      bankNodesList.forEach((n, idx) => {
        n.position.y = Math.sin(elapsedTime * 2 + idx) * 0.06;
      });

      renderer.render(scene, camera);
      animId = requestAnimationFrame(animate);
    };

    animate();

    return () => {
      container.removeEventListener('mousemove', handleMouseMove);
      container.removeEventListener('click', handleClick);
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animId);
      if (renderer.domElement.parentNode) {
        renderer.domElement.parentNode.removeChild(renderer.domElement);
      }
      renderer.dispose();
    };
  }, [onSelectBank]);

  return (
    <div ref={mountRef} className="w-full h-full cursor-pointer relative">
      {/* FLOATING 3D ARCHITECTURAL BADGES */}
      <div className="absolute top-10 left-10 pointer-events-none px-3.5 py-1.5 rounded-full bg-slate-900/90 border border-cyan-500/60 text-cyan-300 text-xs font-mono font-bold shadow-lg backdrop-blur-md flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
        JPMorgan Chase 🗽
      </div>

      <div className="absolute top-10 right-10 pointer-events-none px-3.5 py-1.5 rounded-full bg-slate-900/90 border border-rose-500/60 text-rose-300 text-xs font-mono font-bold shadow-lg backdrop-blur-md flex items-center gap-1.5">
        <span className="text-rose-500">🔴</span>
        HSBC 🏛️
      </div>

      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-20 pointer-events-none px-4 py-2 rounded-2xl bg-indigo-950/90 border-2 border-indigo-400 text-white text-xs font-mono font-extrabold shadow-2xl backdrop-blur-md flex flex-col items-center">
        <span>Enclave</span>
        <div className="w-2 h-2 bg-indigo-400 rotate-45 -mb-3 mt-1" />
      </div>

      <div className="absolute bottom-12 left-10 pointer-events-none px-3.5 py-1.5 rounded-full bg-slate-900/90 border border-purple-500/60 text-purple-300 text-xs font-mono font-bold shadow-lg backdrop-blur-md flex items-center gap-1.5">
        <LockIcon />
        Intel SGX TEE 🔒
      </div>

      <div className="absolute bottom-12 right-10 pointer-events-none px-3.5 py-1.5 rounded-full bg-slate-900/90 border border-cyan-500/60 text-cyan-300 text-xs font-mono font-bold shadow-lg backdrop-blur-md flex items-center gap-1.5">
        <span className="px-1 bg-blue-600 text-white text-[9px] font-bold rounded">DB</span>
        Deutsche Bank 🏢
      </div>
    </div>
  );
}

export default function LandingPage() {
  const navigate = useNavigate();
  const bgCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // Responsive Mobile Menu State
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState<boolean>(false);

  // Hero interactive state
  const [isDpShieldActive, setIsDpShieldActive] = useState<boolean>(true);
  const [activeBankDrawer, setActiveBankDrawer] = useState<BankInfoDetail | null>(null);

  // Telemetry HUD state
  const [flRound, setFlRound] = useState<number>(42);
  const [accuracy, setAccuracy] = useState<number>(98.42);

  // Active Scroll Section State for Quick-Nav Dock
  const [activeSection, setActiveSection] = useState<string>('hero');

  // Section 3: Product Capabilities State
  const [productTab, setProductTab] = useState<'dp' | 'negotiator' | 'sla'>('dp');
  const [epsilonCalc, setEpsilonCalc] = useState<number>(0.5);

  // Section 4: 8-Node Platform Graph State
  const [graphStep, setGraphStep] = useState<number>(1);
  const [isGraphDetected, setIsGraphDetected] = useState<boolean>(false);
  const [isGraphIsolated, setIsGraphIsolated] = useState<boolean>(false);
  const [showAttentionMatrix, setShowAttentionMatrix] = useState<boolean>(false);
  const [selectedGraphNode, setSelectedGraphNode] = useState<GraphNodeDetail | null>(null);

  // Section 5: Architecture Layer Stack State
  const [activeLayer, setActiveLayer] = useState<number>(1);

  // Section 6: Security Attack Simulator State
  const [attackType, setAttackType] = useState<'mia' | 'dlg' | 'byzantine'>('mia');
  const [attackStatus, setAttackStatus] = useState<string>('SAFE (0.00% Leakage Risk)');

  // Section 7: API Playground Multi-Lang SDK State
  const [apiLang, setApiLang] = useState<'curl' | 'python' | 'node' | 'go'>('curl');
  const [apiEndpoint, setApiEndpoint] = useState<string>('handshake');
  const [apiReqBody] = useState<string>(
    JSON.stringify(
      { bank_id: 'jpmorgan_chase', pytorch_version: '2.2.1+cu121', ram_gb: 128.0 },
      null,
      2
    )
  );
  const [apiResponse, setApiResponse] = useState<string | null>(null);
  const [isApiLoading, setIsApiLoading] = useState<boolean>(false);

  // Section 8: Docs Deployment Blueprint State
  const [deployTab, setDeployTab] = useState<'helm' | 'docker' | 'terraform' | 'shell'>('helm');
  const [wizOs, setWizOs] = useState<'linux' | 'macos' | 'windows'>('linux');
  const [wizHardware, setWizHardware] = useState<'cuda' | 'metal' | 'cpu'>('cuda');
  const [wizRegulation, setWizRegulation] = useState<'eu_ai_act' | 'ffiec' | 'fca'>('eu_ai_act');
  const [copiedWizCmd, setCopiedWizCmd] = useState<boolean>(false);

  // Track active section on scroll
  useEffect(() => {
    const handleScroll = () => {
      const sections = ['hero', 'problem-solution', 'how-it-works', 'product', 'platform', 'architecture', 'security', 'api', 'docs'];
      const scrollPosition = window.scrollY + 200;

      for (const sectionId of sections) {
        const el = document.getElementById(sectionId);
        if (el) {
          const top = el.offsetTop;
          const height = el.offsetHeight;
          if (scrollPosition >= top && scrollPosition < top + height) {
            setActiveSection(sectionId);
            break;
          }
        }
      }
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  // Continuous Telemetry Timer
  useEffect(() => {
    const interval = setInterval(() => {
      setFlRound((r) => r + 1);
      setAccuracy((acc) => Number((acc + (Math.random() * 0.06 - 0.03)).toFixed(2)));
    }, 3200);
    return () => clearInterval(interval);
  }, []);

  // Living Distributed Background Topology Mesh Canvas
  useEffect(() => {
    const canvas = bgCanvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animId: number;
    let w = (canvas.width = window.innerWidth);
    let h = (canvas.height = window.innerHeight);

    const handleResize = () => {
      if (!canvas) return;
      w = canvas.width = window.innerWidth;
      h = canvas.height = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    const bgNodes: Array<{ x: number; y: number; vx: number; vy: number; radius: number }> = [];
    const count = w < 640 ? 18 : 35;
    for (let i = 0; i < count; i++) {
      bgNodes.push({
        x: Math.random() * w,
        y: Math.random() * h,
        vx: (Math.random() - 0.5) * 0.4,
        vy: (Math.random() - 0.5) * 0.4,
        radius: 1.5 + Math.random() * 2,
      });
    }

    const drawBg = () => {
      ctx.clearRect(0, 0, w, h);

      for (let i = 0; i < bgNodes.length; i++) {
        const n1 = bgNodes[i];
        if (!n1) continue;
        n1.x += n1.vx;
        n1.y += n1.vy;

        if (n1.x < 0 || n1.x > w) n1.vx *= -1;
        if (n1.y < 0 || n1.y > h) n1.vy *= -1;

        ctx.fillStyle = '#6366f130';
        ctx.beginPath();
        ctx.arc(n1.x, n1.y, n1.radius, 0, Math.PI * 2);
        ctx.fill();

        for (let j = i + 1; j < bgNodes.length; j++) {
          const n2 = bgNodes[j];
          if (!n2) continue;
          const dx = n1.x - n2.x;
          const dy = n1.y - n2.y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < 140) {
            ctx.strokeStyle = `rgba(99, 102, 241, ${0.15 * (1 - dist / 140)})`;
            ctx.lineWidth = 1;
            ctx.beginPath();
            ctx.moveTo(n1.x, n1.y);
            ctx.lineTo(n2.x, n2.y);
            ctx.stroke();
          }
        }
      }

      animId = requestAnimationFrame(drawBg);
    };

    drawBg();

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animId);
    };
  }, []);

  // Security Attack Simulator Logic
  const handleRunAttack = (type: 'mia' | 'dlg' | 'byzantine') => {
    setAttackType(type);
    setAttackStatus('RUNNING SIMULATION...');
    setTimeout(() => {
      if (type === 'mia') {
        setAttackStatus('MIA LEAK RISK: 0.02% (Mathematically Shielded by ε=0.50 DP Noise)');
      } else if (type === 'dlg') {
        setAttackStatus('DLG GRADIENT RECONSTRUCTION BLOCKED: Tensor Reconstruction Error > 99.94%');
      } else {
        setAttackStatus('BYZANTINE NODE ISOLATED: Trimmed-Mean Aggregation Neutralized 100% Malicious Gradients');
      }
    }, 1000);
  };

  // API Playground Execution
  const handleSendApiRequest = () => {
    setIsApiLoading(true);
    setApiResponse(null);
    setTimeout(() => {
      setIsApiLoading(false);
      if (apiEndpoint === 'handshake') {
        setApiResponse(
          JSON.stringify(
            {
              registered: true,
              status: 'COMPATIBLE',
              handshake_token: 'hs_tok_jpm89a2f10b42',
              assigned_quorum: 'cluster_global_tier1',
              timestamp: new Date().toISOString(),
            },
            null,
            2
          )
        );
      } else if (apiEndpoint === 'clients') {
        setApiResponse(
          JSON.stringify(
            [
              { bank: 'JPMorgan Chase', status: 'ONLINE', ram_gb: 128.0, hardware: 'NVIDIA A100' },
              { bank: 'HSBC Holdings', status: 'ONLINE', ram_gb: 64.0, hardware: 'NVIDIA H100' },
              { bank: 'Deutsche Bank', status: 'ONLINE', ram_gb: 32.0, hardware: 'CPU Cluster' },
            ],
            null,
            2
          )
        );
      } else {
        setApiResponse(
          JSON.stringify(
            {
              allowed: true,
              policy_name: 'Compliance_Officer_SAR_Export_V2',
              reason: 'Role clearance level 4 matches Tier 1 dataset clearance requirement.',
              evaluated_at: new Date().toISOString(),
            },
            null,
            2
          )
        );
      }
    }, 800);
  };

  // Multi-Language SDK Code Generator
  const getSdkCode = () => {
    if (apiLang === 'curl') {
      return `curl -X POST https://api.cfi-platform.org/v1/coordinator/${apiEndpoint} \\
  -H "Authorization: Bearer cfi_sec_key_9942a1" \\
  -H "Content-Type: application/json" \\
  -d '${apiReqBody}'`;
    }
    if (apiLang === 'python') {
      return `from cfi_sdk import FederatedCoordinatorClient

client = FederatedCoordinatorClient(api_key="cfi_sec_key_9942a1")
response = client.coordinator.${apiEndpoint}(
    bank_id="jpmorgan_chase",
    pytorch_version="2.2.1+cu121",
    ram_gb=128.0
)
print("Handshake Token:", response.handshake_token)`;
    }
    if (apiLang === 'node') {
      return `import { CFIClient } from '@cfi/sdk';

const client = new CFIClient({ apiKey: 'cfi_sec_key_9942a1' });
const result = await client.coordinator.execute('${apiEndpoint}', {
  bankId: 'jpmorgan_chase',
  pytorchVersion: '2.2.1+cu121'
});
console.log('Quorum Status:', result.status);`;
    }
    return `package main

import (
    "fmt"
    "github.com/cfi-platform/cfi-go/sdk"
)

func main() {
    client := sdk.NewClient("cfi_sec_key_9942a1")
    res, _ := client.Coordinator.Handshake("jpmorgan_chase")
    fmt.Println("Handshake:", res.Token)
}`;
  };

  // Deployment Blueprint Code Generator
  const getDeployBlueprint = () => {
    const accelFlag = wizHardware === 'cuda' ? '--cuda' : wizHardware === 'metal' ? '--metal' : '--cpu';
    const regFlag = wizRegulation === 'eu_ai_act' ? '--compliance eu-ai-act' : '--compliance ffiec';

    if (deployTab === 'helm') {
      return `# Kubernetes Helm values.yaml for CFI Node
replicaCount: 3
hardware:
  acceleration: "${wizHardware}"
  gpuMemory: "80Gi"
enclave:
  type: "intel_sgx"
  dpNoiseEpsilon: 0.50
compliance:
  standard: "${wizRegulation}"
ingress:
  enabled: true
  hostname: node.jpmorgan.cfi-platform.org`;
    }
    if (deployTab === 'docker') {
      return `version: '3.8'
services:
  cfi-bank-node:
    image: ghcr.io/yusufcalisir/cfi-node:v2.4.0
    restart: always
    environment:
      - HARDWARE_ACCEL=${wizHardware}
      - COMPLIANCE_RULE=${wizRegulation}
      - DP_EPSILON=0.50
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]`;
    }
    if (deployTab === 'terraform') {
      return `# AWS Enclave Terraform Blueprint
module "cfi_sgx_node" {
  source = "terraform-aws-modules/enclave/aws"
  instance_type = "c6i.8xlarge"
  enable_sgx    = true
  compliance    = "${wizRegulation}"
  hardware_accel = "${wizHardware}"
}`;
    }
    return `#!/usr/bin/env bash
# Automated Node Installation Script
curl -sSL https://get.cfi-platform.org/install.sh | bash -s -- ${accelFlag} ${regFlag}`;
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 font-sans selection:bg-indigo-500 selection:text-white relative overflow-x-hidden">
      {/* ── LIVING DISTRIBUTED BACKGROUND MESH CANVAS ────────── */}
      <canvas ref={bgCanvasRef} className="fixed inset-0 w-full h-full pointer-events-none z-0 opacity-20" />

      {/* ── FLOATING QUICK-NAV DOCK (RIGHT SIDEBAR) ───────────── */}
      <div className="fixed right-4 top-1/2 -translate-y-1/2 z-40 hidden lg:flex flex-col items-center gap-3 p-2.5 rounded-full bg-slate-900/70 border border-slate-800/80 backdrop-blur-md shadow-2xl">
        {[
          { id: 'hero', label: 'Architecture Topology' },
          { id: 'problem-solution', label: 'The Core Problem' },
          { id: 'how-it-works', label: 'How It Works' },
          { id: 'product', label: 'Privacy Engine' },
          { id: 'platform', label: 'GNN Graph Collusion' },
          { id: 'architecture', label: '5-Layer Specs' },
          { id: 'security', label: 'Attack Simulator' },
          { id: 'api', label: 'REST & SDK Studio' },
          { id: 'docs', label: 'Deploy Blueprint' },
        ].map((item) => (
          <a
            key={item.id}
            href={`#${item.id}`}
            title={item.label}
            className={`group relative h-3 w-3 rounded-full transition-all duration-300 flex items-center justify-center ${
              activeSection === item.id
                ? 'bg-indigo-400 ring-4 ring-indigo-500/30 scale-125'
                : 'bg-slate-700 hover:bg-slate-400'
            }`}
          >
            <span className="absolute right-6 px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 text-slate-200 text-[10px] font-mono font-bold whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none shadow-lg">
              {item.label}
            </span>
          </a>
        ))}
      </div>

      <div className="relative z-10">
        {/* ── SECTION 1: SLEEK UNCLUTTERED FLOATING NAVBAR ── */}
        <header className="sticky top-0 z-50 backdrop-blur-xl bg-slate-950/80 border-b border-slate-800/80 h-16 flex items-center">
          <div className="w-full max-w-7xl mx-auto px-4 sm:px-6 flex items-center justify-between">
            {/* Logo */}
            <div className="flex items-center gap-3 cursor-pointer" onClick={() => navigate('/')}>
              <CfiLogoMark className="w-10 h-10 drop-shadow-[0_0_12px_rgba(99,102,241,0.5)]" />
              <div className="flex items-center gap-2">
                <span className="font-extrabold text-base tracking-tight bg-gradient-to-r from-white via-slate-200 to-slate-400 bg-clip-text text-transparent">
                  CF-Intelligence
                </span>
                <span className="px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 font-mono text-[10px] font-bold border border-indigo-500/30">
                  v2.4.0
                </span>
              </div>
            </div>

            {/* Desktop Navigation Links */}
            <nav className="hidden lg:flex items-center gap-8 text-xs font-semibold text-slate-300 bg-slate-900/60 border border-slate-800/80 rounded-full px-7 py-2.5 backdrop-blur-md shadow-inner">
              <a href="#hero" className="hover:text-indigo-400 transition-colors">Overview</a>
              <a href="#platform" className="hover:text-indigo-400 transition-colors">Platform</a>
              <a href="#architecture" className="hover:text-indigo-400 transition-colors">Architecture</a>
              <a href="#api" className="hover:text-indigo-400 transition-colors">API & Docs</a>
            </nav>

            {/* Actions */}
            <div className="flex items-center gap-3">
              <div className="hidden xl:flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-semibold">
                <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                Quorum Active (3/3 Synced)
              </div>

              <button
                onClick={() => navigate('/dashboard')}
                className="hidden sm:inline-flex group relative items-center justify-center p-0.5 overflow-hidden rounded-xl font-bold text-xs shadow-lg shadow-indigo-500/25 transition-all duration-300 hover:scale-105"
              >
                <span className="absolute inset-0 bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 animate-gradient" />
                <span className="relative px-4 py-2 rounded-[10px] bg-slate-950 text-white flex items-center gap-2 group-hover:bg-slate-900 transition-all">
                  <span>Launch Demo</span>
                  <ArrowRightIcon />
                </span>
              </button>

              {/* Mobile Hamburger Toggle Button */}
              <button
                onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
                className="lg:hidden p-2 rounded-xl bg-slate-900 border border-slate-800 text-slate-200 focus:outline-none"
                aria-label="Toggle Navigation Menu"
              >
                {isMobileMenuOpen ? <CloseIcon /> : <MenuIcon />}
              </button>
            </div>
          </div>
        </header>

        {/* ── RESPONSIVE MOBILE MENU OVERLAY ──────────────────── */}
        <AnimatePresence>
          {isMobileMenuOpen && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="lg:hidden border-b border-slate-800 bg-slate-950/95 backdrop-blur-2xl px-6 py-6 space-y-4"
            >
              <nav className="flex flex-col space-y-3 font-semibold text-sm text-slate-200">
                <a href="#hero" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Overview
                </a>
                <a href="#platform" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Platform
                </a>
                <a href="#architecture" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  Architecture
                </a>
                <a href="#api" onClick={() => setIsMobileMenuOpen(false)} className="p-2.5 rounded-xl hover:bg-slate-900 text-slate-300">
                  API & Docs
                </a>
              </nav>

              <button
                onClick={() => {
                  setIsMobileMenuOpen(false);
                  navigate('/dashboard');
                }}
                className="w-full py-3 rounded-xl bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 text-white font-bold text-xs shadow-lg shadow-indigo-500/25 flex items-center justify-center gap-2"
              >
                <span>Launch Live Platform Demo</span>
                <ArrowRightIcon />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── SECTION 2: HERO WITH REFERENCE-ACCURATE 3D WEBGL GRAPHICS CANVAS ── */}
        <section id="hero" className="min-h-[calc(100vh-5rem)] flex items-center justify-center relative py-8 px-4 sm:px-6 max-w-7xl mx-auto overflow-hidden">
          {/* Glowing Background Radial Orbs */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[500px] bg-gradient-to-tr from-indigo-500/10 via-purple-500/10 to-emerald-500/10 rounded-full blur-3xl pointer-events-none" />

          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-center w-full">
            {/* ── LEFT COLUMN: Headline, Text & Value Pillars ── */}
            <div className="lg:col-span-6 space-y-6 text-left">
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
                className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 text-xs font-semibold"
              >
                <span>✨ PRIVACY-PRESERVING COLLABORATIVE AI</span>
                <span className="text-indigo-500">•</span>
                <span className="text-emerald-400 font-bold">EU AI Act & FinCEN Compliant</span>
              </motion.div>

              <motion.h1
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.1 }}
                className="text-3xl sm:text-5xl lg:text-5xl font-black tracking-tight leading-tight text-slate-100"
              >
                Stop Cross-Bank Money Laundering Syndicates with Federated AI
              </motion.h1>

              <motion.p
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.2 }}
                className="text-xs sm:text-base text-slate-300 leading-relaxed"
              >
                JPMorgan Chase, HSBC, and Deutsche Bank train a joint Graph Neural Network model to detect money mule rings <strong className="text-emerald-400">without sharing raw customer data, PII, or internal transaction logs</strong>.
              </motion.p>

              {/* 3 Core Value Pillars */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.22 }}
                className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-1"
              >
                <div className="p-3 rounded-xl bg-slate-900/60 border border-indigo-500/20">
                  <span className="text-xs font-bold text-indigo-300 block">🔒 100% Data Privacy</span>
                  <span className="text-[11px] text-slate-400 mt-0.5 block">Differential Privacy ($\epsilon=0.50$) guarantees zero raw data leakage.</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-900/60 border border-purple-500/20">
                  <span className="text-xs font-bold text-purple-300 block">⚡ 98.4% Ring Accuracy</span>
                  <span className="text-[11px] text-slate-400 mt-0.5 block">Streaming GNN pinpoints cross-bank money mule rings in &lt; 3.2ms.</span>
                </div>
                <div className="p-3 rounded-xl bg-slate-900/60 border border-emerald-500/20">
                  <span className="text-xs font-bold text-emerald-300 block">🏛️ Full Compliance</span>
                  <span className="text-[11px] text-slate-400 mt-0.5 block">Native ISO 20022 XML parsing and automated FinCEN SAR reporting.</span>
                </div>
              </motion.div>

              {/* CTA Buttons */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.25 }}
                className="flex flex-wrap items-center gap-4 pt-2"
              >
                <button
                  onClick={() => navigate('/dashboard')}
                  className="px-6 py-3 rounded-xl bg-gradient-to-r from-indigo-500 via-purple-500 to-emerald-500 text-white font-bold text-xs shadow-lg shadow-indigo-500/25 hover:scale-105 transition-all flex items-center gap-2"
                >
                  <span>Launch Live Platform Demo</span>
                  <ArrowRightIcon />
                </button>
                <a
                  href="#problem-solution"
                  className="px-5 py-3 rounded-xl bg-slate-900 hover:bg-slate-800 text-slate-300 font-bold text-xs border border-slate-800 transition-all"
                >
                  The Problem & Solution ↓
                </a>
              </motion.div>

              {/* Live Telemetry HUD Bar */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.3 }}
                className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 rounded-2xl bg-slate-900/60 border border-slate-800/80 backdrop-blur-xl"
              >
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Active FL Round</span>
                  <div className="text-lg font-black text-indigo-400 font-mono mt-0.5">#{flRound}</div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Global Accuracy</span>
                  <div className="text-lg font-black text-emerald-400 font-mono mt-0.5">{accuracy}%</div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Privacy (ε)</span>
                  <div className="text-lg font-black text-purple-400 font-mono mt-0.5">0.50 (Strict)</div>
                </div>
                <div>
                  <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">Stream Speed</span>
                  <div className="text-lg font-black text-blue-400 font-mono mt-0.5">1.4 GB/s</div>
                </div>
              </motion.div>
            </div>

            {/* ── RIGHT COLUMN: REFERENCE-ACCURATE 3D WEBGL ARCHITECTURE VISUALIZATION ── */}
            <div className="lg:col-span-6 relative w-full h-[540px] rounded-3xl border border-indigo-500/30 bg-slate-950/80 p-2 shadow-2xl overflow-hidden flex flex-col justify-between">
              {/* Header Controls */}
              <div className="relative z-10 flex items-center justify-between p-3 border-b border-slate-800/60 bg-slate-950/60 backdrop-blur-md rounded-t-2xl">
                <div className="flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 animate-ping" />
                  <span className="text-[11px] font-mono font-bold text-indigo-300 uppercase">
                    CROSS-BANK FEDERATED TOPOLOGY MESH
                  </span>
                </div>
                <button
                  onClick={() => setIsDpShieldActive(!isDpShieldActive)}
                  className="px-2.5 py-1 rounded-lg text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1"
                >
                  <LockIcon />
                  <span>DP Shield: {isDpShieldActive ? 'ON' : 'OFF'}</span>
                </button>
              </div>

              {/* REFERENCE-ACCURATE 3D CANVAS */}
              <div className="relative z-0 w-full h-[430px] my-auto">
                <Real3DBankScene onSelectBank={(bank) => setActiveBankDrawer(bank)} />
              </div>

              {/* Footer Instruction Bar */}
              <div className="relative z-10 p-2 border-t border-slate-800/60 bg-slate-950/60 backdrop-blur-md rounded-b-2xl flex items-center justify-between text-[10px] font-mono text-slate-400">
                <span>Hover & Move mouse to rotate 3D camera view</span>
                <span className="text-indigo-400 font-bold">CLICK ANY 3D NODE TO INSPECT 🔍</span>
              </div>
            </div>
          </div>
        </section>

        {/* ── BANK INSPECTOR DRAWER (SLIDING OVERLAY) ──────────────── */}
        <AnimatePresence>
          {activeBankDrawer && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setActiveBankDrawer(null)}
              className="fixed inset-0 bg-slate-950/80 backdrop-blur-md z-50 flex justify-end"
            >
              <motion.div
                initial={{ x: '100%' }}
                animate={{ x: 0 }}
                exit={{ x: '100%' }}
                transition={{ type: 'spring', damping: 25 }}
                onClick={(e) => e.stopPropagation()}
                className="w-full sm:max-w-xl bg-slate-900 border-l border-slate-800 p-6 sm:p-8 overflow-y-auto space-y-6"
              >
                <div className="flex items-center justify-between border-b border-slate-800 pb-4">
                  <div>
                    <span className="text-xs font-mono font-bold text-indigo-400 uppercase">
                      REAL BANK HARDWARE NODE • {activeBankDrawer.ticker}
                    </span>
                    <h3 className="text-lg sm:text-xl font-bold text-slate-100">{activeBankDrawer.name}</h3>
                    <p className="text-xs text-slate-400 mt-0.5">{activeBankDrawer.location}</p>
                  </div>
                  <button
                    onClick={() => setActiveBankDrawer(null)}
                    className="px-3 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs font-bold text-slate-300"
                  >
                    Close ✖
                  </button>
                </div>

                <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
                    <span className="text-slate-500">Hardware Accelerator:</span>
                    <div className="text-indigo-300 font-bold mt-0.5 truncate">{activeBankDrawer.hardware}</div>
                  </div>
                  <div className="p-3 rounded-xl bg-slate-950 border border-slate-800">
                    <span className="text-slate-500">Host Memory & Latency:</span>
                    <div className="text-emerald-300 font-bold mt-0.5">
                      {activeBankDrawer.ram} ({activeBankDrawer.latency})
                    </div>
                  </div>
                </div>

                <div>
                  <h4 className="text-xs font-bold text-slate-300 uppercase tracking-wider mb-2">
                    Live ISO 20022 Stream Logs
                  </h4>
                  <div className="p-4 rounded-xl bg-slate-950 font-mono text-[11px] text-slate-300 border border-slate-800 space-y-2 overflow-x-auto">
                    {activeBankDrawer.xmlLogs.map((log, idx) => (
                      <div key={idx} className="leading-relaxed border-b border-slate-900 pb-2">
                        <span className="text-indigo-400 font-bold">[LOG-{idx + 1}]</span> {log}
                      </div>
                    ))}
                  </div>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-indigo-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION: THE CROSS-BANK FRAUD BLIND SPOT (THE PROBLEM & SOLUTION) ── */}
        <motion.section
          id="problem-solution"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs font-bold uppercase tracking-wider">
              THE CORE FINANCIAL PROBLEM
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
              The Cross-Bank Money Laundering Blind Spot
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Why traditional single-bank fraud systems are blind to modern organized crime rings.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div className="p-6 sm:p-8 rounded-3xl bg-slate-900/60 border border-rose-500/30 space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">⚠️</span>
                <h3 className="text-xl font-bold text-rose-300">The Problem: Institutional Blindness</h3>
              </div>
              <ul className="space-y-3 text-xs sm:text-sm text-slate-300 leading-relaxed">
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">1.</span>
                  <span><strong>Multi-Bank Smurfing:</strong> Criminal syndicates split $10M illicit funds into $9,500 transfers across JPMorgan, HSBC, and Deutsche Bank.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">2.</span>
                  <span><strong>Isolated Datasets:</strong> Each bank only sees its own internal transaction slice and marks the transfer as "normal retail behavior".</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-rose-400 font-bold">3.</span>
                  <span><strong>Legal Data Sharing Ban:</strong> GDPR, CCPA, and Banking Secrecy laws strictly forbid banks from pooling raw customer records.</span>
                </li>
              </ul>
            </div>

            <div className="p-6 sm:p-8 rounded-3xl bg-slate-900/60 border border-emerald-500/30 space-y-4">
              <div className="flex items-center gap-3">
                <span className="text-2xl">🛡️</span>
                <h3 className="text-xl font-bold text-emerald-300">The Solution: CFI Federated AI</h3>
              </div>
              <ul className="space-y-3 text-xs sm:text-sm text-slate-300 leading-relaxed">
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">1.</span>
                  <span><strong>Privacy-Preserving Federated Learning:</strong> AI model comes to the bank's local server. Customer PII never leaves the firewall.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">2.</span>
                  <span><strong>Streaming Graph Neural Networks:</strong> Connects account topology subgraphs across banks to pinpoint money mule rings in &lt; 3.2ms.</span>
                </li>
                <li className="flex items-start gap-2">
                  <span className="text-emerald-400 font-bold">3.</span>
                  <span><strong>Intel SGX TEE & Differential Privacy:</strong> Encrypted model weights are aggregated in hardware enclaves with zero data reconstruction risk.</span>
                </li>
              </ul>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-indigo-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION: HOW IT WORKS 4-STEP INTERACTIVE WORKFLOW ── */}
        <motion.section
          id="how-it-works"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold uppercase tracking-wider">
              ENTERPRISE FEDERATED ARCHITECTURE
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
              How Collaborative Fraud Detection Works
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              A 4-step privacy-preserving workflow operating across JPMorgan Chase, HSBC, and Deutsche Bank.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-indigo-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-indigo-500/20 border border-indigo-500/30 flex items-center justify-center font-mono font-bold text-indigo-400 text-sm">
                01
              </div>
              <h3 className="text-base font-bold text-slate-100">Local ISO 20022 Data Intake</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Each member bank parses SWIFT <code className="text-indigo-300">pacs.008</code> and <code className="text-indigo-300">camt.053</code> transaction XML files within its own secure firewall. Raw customer identity never leaves the bank.
              </p>
            </div>

            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-purple-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-purple-500/20 border border-purple-500/30 flex items-center justify-center font-mono font-bold text-purple-400 text-sm">
                02
              </div>
              <h3 className="text-base font-bold text-slate-100">PyTorch GNN Local Training</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Local Graph Attention Networks (GAT) convert account transaction topology into high-dimensional embedding vectors (h_v), isolating local smurfing behavior.
              </p>
            </div>

            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-emerald-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-emerald-500/20 border border-emerald-500/30 flex items-center justify-center font-mono font-bold text-emerald-400 text-sm">
                03
              </div>
              <h3 className="text-base font-bold text-slate-100">Intel SGX Enclave Aggregation</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                Encrypted model weights are uploaded into an Intel SGX Hardware TEE Enclave. Differential Privacy noise ($\epsilon=0.50$) is injected during FedAvg consensus.
              </p>
            </div>

            <div className="p-6 rounded-3xl bg-slate-900/60 border border-slate-800 hover:border-cyan-500/40 transition-all space-y-4">
              <div className="h-10 w-10 rounded-2xl bg-cyan-500/20 border border-cyan-500/30 flex items-center justify-center font-mono font-bold text-cyan-400 text-sm">
                04
              </div>
              <h3 className="text-base font-bold text-slate-100">Global Ring Isolation & SAR</h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                The updated global model is pushed back to all member banks. High-risk cross-bank money mule rings are instantly frozen and exported as FinCEN SAR XML filings.
              </p>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-purple-500 to-emerald-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 4: PRODUCT CAPABILITIES & PRIVACY ENGINE ──────── */}
        <motion.section
          id="product"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-indigo-500/10 border border-indigo-500/20 text-indigo-400 text-xs font-bold uppercase tracking-wider">
              ENTERPRISE PRODUCT CAPABILITIES
            </span>
            <h2 className="text-3xl sm:text-4xl font-extrabold text-slate-100">
              Differential Privacy & Heterogeneous FL Engine
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Explore real-time mathematical privacy guarantees, cross-bank model parameter negotiators, and detection SLAs.
            </p>

            <div className="flex justify-center gap-2 pt-4">
              {[
                { id: 'dp', label: '🔒 DP Noise Simulator' },
                { id: 'negotiator', label: '🧠 Heterogeneous Negotiator' },
                { id: 'sla', label: '⚡ Detection SLAs & Benchmarks' },
              ].map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setProductTab(tab.id as any)}
                  className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${
                    productTab === tab.id
                      ? 'bg-indigo-600 text-white shadow-lg shadow-indigo-600/30'
                      : 'bg-slate-900 border border-slate-800 text-slate-400 hover:bg-slate-800'
                  }`}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            <div className="p-3.5 rounded-2xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-xs text-indigo-200">
              <span className="font-bold text-indigo-400 block mb-1">PROJECT ARCHITECTURE & IMPACT:</span>
              {productTab === 'dp' && 'Differential Privacy (ε) injects calibrated Gaussian noise into model gradients, mathematically guaranteeing zero customer identity reconstruction.'}
              {productTab === 'negotiator' && 'Heterogeneous parameter negotiation aligns model architectures across diverse banking compute infrastructure (A100 GPU vs H100 vs CPU).'}
              {productTab === 'sla' && 'Consortium cross-bank data increases detection precision to 98.42% while reducing false positive alerts by 74.6%.'}
            </div>

            {productTab === 'dp' && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
                <div className="lg:col-span-5 space-y-6">
                  <div>
                    <div className="flex justify-between items-center mb-2">
                      <label className="text-xs font-bold text-slate-300 uppercase">Privacy Budget ($\epsilon$)</label>
                      <span className="text-sm font-mono font-bold text-indigo-400">{epsilonCalc.toFixed(1)}</span>
                    </div>
                    <input
                      type="range"
                      min="0.1"
                      max="5.0"
                      step="0.1"
                      value={epsilonCalc}
                      onChange={(e) => setEpsilonCalc(Number(e.target.value))}
                      className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
                    />
                  </div>

                  <div className="space-y-3 font-mono text-xs">
                    <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center">
                      <span className="text-slate-400">Reconstruction Guarantee:</span>
                      <span className="text-emerald-400 font-bold">
                        {epsilonCalc <= 0.5 ? 'Mathematically Impossible' : epsilonCalc <= 1.5 ? 'Very High' : 'Moderate'}
                      </span>
                    </div>
                    <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 flex justify-between items-center">
                      <span className="text-slate-400">Gaussian Noise Std Dev ($\sigma$):</span>
                      <span className="text-purple-400 font-bold">{(0.5 / epsilonCalc).toFixed(3)}</span>
                    </div>
                  </div>
                </div>

                <div className="lg:col-span-7 grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="p-6 rounded-2xl bg-slate-950 border border-emerald-500/30 flex flex-col justify-between">
                    <span className="text-xs text-slate-400 uppercase font-bold">Estimated Model Accuracy</span>
                    <div className="text-3xl sm:text-4xl font-black text-emerald-400 font-mono mt-4">
                      {(88 + (epsilonCalc / 5.0) * 11.2).toFixed(2)}%
                    </div>
                    <span className="text-[10px] text-slate-500 mt-2">Verified against 1.2M Synthetic SWIFT pacs.008 transactions</span>
                  </div>

                  <div className="p-6 rounded-2xl bg-slate-950 border border-purple-500/30 flex flex-col justify-between">
                    <span className="text-xs text-slate-400 uppercase font-bold">Training Loss Convergence</span>
                    <div className="text-3xl sm:text-4xl font-black text-purple-400 font-mono mt-4">
                      {(0.18 - (epsilonCalc / 5.0) * 0.14).toFixed(4)}
                    </div>
                    <span className="text-[10px] text-slate-500 mt-2">Smooth gradient decay without straggler node distortion</span>
                  </div>
                </div>
              </div>
            )}

            {productTab === 'negotiator' && (
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 font-mono text-xs">
                <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 space-y-3">
                  <span className="text-indigo-400 font-bold uppercase block">JPMorgan Chase Node</span>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Arch: PyTorch GAT</div>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Layer Align: 512 ➔ 256 Embedding</div>
                  <div className="text-emerald-400 font-bold">Quorum Status: MATCHED (100%)</div>
                </div>
                <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 space-y-3">
                  <span className="text-purple-400 font-bold uppercase block">HSBC Holdings Node</span>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Arch: PyTorch GCN</div>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Layer Align: 512 ➔ 256 Embedding</div>
                  <div className="text-emerald-400 font-bold">Quorum Status: MATCHED (100%)</div>
                </div>
                <div className="p-5 rounded-2xl bg-slate-950 border border-slate-800 space-y-3">
                  <span className="text-emerald-400 font-bold uppercase block">Deutsche Bank AG Node</span>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Arch: PyTorch GraphSAGE</div>
                  <div className="p-2.5 rounded bg-slate-900 text-slate-300">Layer Align: 256 ➔ Batch Scale 32</div>
                  <div className="text-emerald-400 font-bold">Quorum Status: ADAPTED</div>
                </div>
              </div>
            )}

            {productTab === 'sla' && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center font-mono">
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">Detection Precision</span>
                  <div className="text-3xl font-black text-emerald-400 mt-2">98.42%</div>
                </div>
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">False Positive Reduction</span>
                  <div className="text-3xl font-black text-indigo-400 mt-2">-74.6%</div>
                </div>
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">Max Stream Throughput</span>
                  <div className="text-3xl font-black text-purple-400 mt-2">15,000 TPS</div>
                </div>
                <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800">
                  <span className="text-slate-400 text-xs uppercase font-bold block">Federated FL Latency</span>
                  <div className="text-3xl font-black text-cyan-400 mt-2">&lt; 3.2ms</div>
                </div>
              </div>
            )}
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-purple-500 to-emerald-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 5: PLATFORM & 8-NODE GNN GRAPH COLLUSION SIMULATOR ── */}
        <motion.section
          id="platform"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-purple-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-8">
            <div className="p-3.5 rounded-2xl bg-purple-950/80 border border-purple-500/30 font-mono text-xs text-purple-200">
              <span className="font-bold text-purple-400 block mb-1">PROJECT ARCHITECTURE & IMPACT:</span>
              Criminal syndicates split funds into sub-$10K transfers across multiple institutions (smurfing/layering). Streaming Graph Neural Networks merge cross-bank subgraphs to detect money mule rings instantly.
            </div>

            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-purple-400 uppercase tracking-wider">
                  STREAMING GNN COLLUSION DETECTOR
                </span>
                <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-100 mt-1">
                  Cross-Bank Money Laundering Ring Detection Engine
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  How Streaming Graph Neural Networks detect multi-institutional smurfing and layering rings in zero-trust environments.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => setIsGraphDetected(!isGraphDetected)}
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold shadow-md transition-all"
                >
                  {isGraphDetected ? 'Reset Graph 🔄' : 'Run GNN Detection 🧠'}
                </button>

                {isGraphDetected && (
                  <button
                    onClick={() => setIsGraphIsolated(!isGraphIsolated)}
                    className="px-4 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold shadow-md transition-all"
                  >
                    {isGraphIsolated ? 'Reconnect Subgraphs 🔗' : 'Isolate Fraud Ring ✂️'}
                  </button>
                )}

                <button
                  onClick={() => setShowAttentionMatrix(!showAttentionMatrix)}
                  className="px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs font-bold border border-slate-700"
                >
                  {showAttentionMatrix ? 'Hide Matrix' : 'GAT Matrix (α_ij)'}
                </button>
              </div>
            </div>

            {/* 4-Step Narrative Timeline Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { step: 1, title: '1. Multi-Bank Layering', desc: 'Organized crime rings split funds below $10K AML thresholds across JPMorgan, HSBC, and Deutsche Bank.' },
                { step: 2, title: '2. Local GAT Embeddings', desc: 'Each bank computes local PyTorch GNN node feature vectors without exposing PII.' },
                { step: 3, title: '3. Enclave Subgraph Match', desc: 'Intel SGX TEE reconstructs cross-bank adjacency edges using zero-knowledge proofs.' },
                { step: 4, title: '4. Edge Severance & SAR', desc: 'Mule accounts are isolated across all member banks simultaneously with FinCEN SAR XML filings.' },
              ].map((s) => (
                <div
                  key={s.step}
                  onClick={() => setGraphStep(s.step)}
                  className={`p-4 rounded-2xl border cursor-pointer transition-all ${
                    graphStep === s.step
                      ? 'bg-indigo-950/60 border-indigo-500 text-slate-100 shadow-lg shadow-indigo-500/20 ring-1 ring-indigo-500/40'
                      : 'bg-slate-950/50 border-slate-800 text-slate-400 hover:border-slate-700'
                  }`}
                >
                  <span className="text-[10px] font-mono font-bold text-indigo-400 uppercase block">STEP {s.step}</span>
                  <h4 className="text-xs font-extrabold text-slate-200 mt-1">{s.title}</h4>
                  <p className="text-[11px] text-slate-400 mt-1.5 leading-relaxed">{s.desc}</p>
                </div>
              ))}
            </div>

            {/* Interactive 8-Node SVG Topology Graph */}
            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-center">
              <div className="lg:col-span-8 relative w-full h-[360px] sm:h-[380px] bg-slate-950 rounded-2xl border border-slate-800 flex items-center justify-center overflow-hidden">
                <svg className="w-full h-full" viewBox="0 0 700 340">
                  {!isGraphIsolated && (
                    <>
                      <line x1="120" y1="170" x2="240" y2="80" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="240" y1="80" x2="380" y2="80" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="380" y1="80" x2="560" y2="170" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="120" y1="170" x2="240" y2="260" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="240" y1="260" x2="380" y2="260" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                      <line x1="380" y1="260" x2="560" y2="170" stroke={isGraphDetected ? '#ef4444' : '#475569'} strokeWidth={isGraphDetected ? '3' : '1.5'} strokeDasharray={isGraphDetected ? '4 4' : 'none'} />
                    </>
                  )}

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.jpm ?? null)} className="cursor-pointer">
                    <circle cx="120" cy="170" r="26" fill="#1e1b4b" stroke="#6366f1" strokeWidth="2" />
                    <text x="120" y="174" fill="#ffffff" fontSize="9" fontWeight="bold" textAnchor="middle">JPM-ACCT</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.shell_a ?? null)} className="cursor-pointer">
                    <circle cx="240" cy="80" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#a855f7'} strokeWidth="2" />
                    <text x="240" y="84" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SHELL-A</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.smurf_1 ?? null)} className="cursor-pointer">
                    <circle cx="380" cy="80" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#a855f7'} strokeWidth="2" />
                    <text x="380" y="84" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SMURF-1</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.hsbc ?? null)} className="cursor-pointer">
                    <circle cx="560" cy="170" r="26" fill="#064e3b" stroke="#10b981" strokeWidth="2" />
                    <text x="560" y="174" fill="#ffffff" fontSize="9" fontWeight="bold" textAnchor="middle">HSBC-ACCT</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.shell_b ?? null)} className="cursor-pointer">
                    <circle cx="240" cy="260" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#ec4899'} strokeWidth="2" />
                    <text x="240" y="264" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SHELL-B</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.smurf_2 ?? null)} className="cursor-pointer">
                    <circle cx="380" cy="260" r="24" fill={isGraphDetected ? '#450a0a' : '#1e1b4b'} stroke={isGraphDetected ? '#ef4444' : '#ec4899'} strokeWidth="2" />
                    <text x="380" y="264" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">SMURF-2</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.db ?? null)} className="cursor-pointer">
                    <circle cx="310" cy="170" r="22" fill="#1e293b" stroke="#38bdf8" strokeWidth="2" />
                    <text x="310" y="174" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">DB-RELAY</text>
                  </g>

                  <g onClick={() => setSelectedGraphNode(GRAPH_NODES_DATA.offramp ?? null)} className="cursor-pointer">
                    <circle cx="450" cy="170" r="22" fill={isGraphDetected ? '#7f1d1d' : '#1e293b'} stroke={isGraphDetected ? '#f87171' : '#f59e0b'} strokeWidth="2" />
                    <text x="450" y="174" fill="#ffffff" fontSize="8" fontWeight="bold" textAnchor="middle">OFFRAMP</text>
                  </g>
                </svg>

                {isGraphDetected && (
                  <div className="absolute top-4 right-4 px-3 py-1.5 rounded-xl bg-rose-500/20 border border-rose-500/40 text-rose-300 font-mono text-[10px] sm:text-xs font-bold">
                    ⚠️ HIGH-RISK RING DETECTED (GNN Confidence: 98.6%)
                  </div>
                )}
              </div>

              {/* Node Inspector Sidebar Panel */}
              <div className="lg:col-span-4 p-5 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs space-y-4 h-[360px] sm:h-[380px] flex flex-col justify-between">
                {selectedGraphNode ? (
                  <div>
                    <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                      <span className="text-[10px] font-bold text-indigo-400 uppercase">NODE TELEMETRY INSPECTOR</span>
                      <span className={`px-2 py-0.5 rounded text-[9px] font-bold ${selectedGraphNode.riskScore > 0.8 ? 'bg-rose-500/20 text-rose-300 border border-rose-500/30' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'}`}>
                        {selectedGraphNode.status}
                      </span>
                    </div>
                    <h3 className="text-sm font-extrabold text-slate-100 mt-2">{selectedGraphNode.name}</h3>
                    <p className="text-[11px] text-slate-400 mt-0.5">{selectedGraphNode.bank}</p>

                    <div className="mt-4 space-y-2 text-[11px]">
                      <div className="p-2 rounded bg-slate-900 flex justify-between">
                        <span className="text-slate-500">GNN Anomaly Score:</span>
                        <span className={`font-bold ${selectedGraphNode.riskScore > 0.8 ? 'text-rose-400' : 'text-emerald-400'}`}>
                          {selectedGraphNode.riskScore}
                        </span>
                      </div>
                      <div className="p-2 rounded bg-slate-900 flex justify-between">
                        <span className="text-slate-500">Transaction Velocity:</span>
                        <span className="text-indigo-300 font-bold">{selectedGraphNode.velocity}</span>
                      </div>
                      <div className="p-2 rounded bg-slate-900 flex justify-between">
                        <span className="text-slate-500">Layering Index:</span>
                        <span className="text-purple-300 font-bold">{selectedGraphNode.anomalyIndex}</span>
                      </div>
                    </div>

                    <p className="text-[10px] text-slate-400 mt-3 leading-relaxed border-t border-slate-900 pt-2">
                      {selectedGraphNode.description}
                    </p>
                  </div>
                ) : (
                  <div className="my-auto text-center space-y-2">
                    <span className="text-2xl">🔍</span>
                    <h4 className="text-xs font-bold text-slate-300">Click Any Graph Node</h4>
                    <p className="text-[11px] text-slate-500">Select any node on the topology map to inspect GNN risk metrics and transaction velocity.</p>
                  </div>
                )}

                <div className="p-2.5 rounded-xl bg-slate-900 text-[10px] text-slate-400 border border-slate-800 text-center">
                  Note: Click "Run GNN Detection" to isolate suspicious money mule rings in real time.
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-emerald-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-emerald-500 to-indigo-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 6: ARCHITECTURE 5-LAYER DEEP SPECIFICATION MATRIX ── */}
        <motion.section
          id="architecture"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="text-center max-w-3xl mx-auto space-y-3">
            <span className="px-3.5 py-1.5 rounded-full bg-purple-500/10 border border-purple-500/20 text-purple-400 text-xs font-bold uppercase tracking-wider">
              5-LAYER SYSTEM ARCHITECTURE
            </span>
            <h2 className="text-2xl sm:text-4xl font-extrabold text-slate-100">
              Interactive Technical Specification Matrix
            </h2>
            <p className="text-xs sm:text-sm text-slate-400">
              Deep dive into every technical layer from raw SWIFT intake to automated SAR export.
            </p>
          </div>

          <div className="glass-card border border-purple-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            <div className="p-3.5 rounded-2xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-xs text-indigo-200">
              <span className="font-bold text-indigo-400 block mb-1">PROJECT ARCHITECTURE & IMPACT:</span>
              Demonstrates the end-to-end enterprise pipeline, from SWIFT ISO 20022 XML parsing and Intel SGX hardware encryption to automated FinCEN SAR reporting.
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
              <div className="lg:col-span-5 space-y-3">
                {[
                  { id: 1, name: 'Layer 1: ISO 20022 Data Intake Engine' },
                  { id: 2, name: 'Layer 2: GNN Subgraph Feature Store' },
                  { id: 3, name: 'Layer 3: Secure Enclave & DP Shield' },
                  { id: 4, name: 'Layer 4: Federated Aggregation Core' },
                  { id: 5, name: 'Layer 5: Automated Regulatory SIEM Exporter' },
                ].map((layer) => (
                  <div
                    key={layer.id}
                    onClick={() => setActiveLayer(layer.id)}
                    className={`p-4 rounded-xl border cursor-pointer transition-all ${
                      activeLayer === layer.id
                        ? 'bg-indigo-600 text-white border-indigo-500 font-bold shadow-lg shadow-indigo-600/20'
                        : 'bg-slate-900/60 border-slate-800 text-slate-300 hover:bg-slate-800/60'
                    }`}
                  >
                    <span className="text-xs font-mono">{layer.name}</span>
                  </div>
                ))}
              </div>

              <div className="lg:col-span-7 p-6 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between space-y-4">
                <div>
                  <span className="text-xs font-mono text-indigo-400 uppercase font-bold">SPECIFICATION MATRIX</span>
                  <h3 className="text-base sm:text-lg font-bold text-slate-100 mt-1">
                    {activeLayer === 1 && 'Native ISO 20022 Financial XML Intake'}
                    {activeLayer === 2 && 'PyTorch Geometric GNN Embedding Feature Store'}
                    {activeLayer === 3 && 'Intel SGX Secure Enclave & Paillier Homomorphic Encryption'}
                    {activeLayer === 4 && 'Byzantine-Robust FedAvg Aggregation Core'}
                    {activeLayer === 5 && 'Automated SAR XML & Splunk SIEM Integration'}
                  </h3>
                  <p className="text-xs text-slate-400 mt-2 leading-relaxed">
                    {activeLayer === 1 && 'Parses pacs.008 and camt.053 XML financial transaction messages directly into local graph tensors without storing customer identity details.'}
                    {activeLayer === 2 && 'Extracts structural graph attention embeddings (GAT) capturing account transaction topologies across isolated banking domains.'}
                    {activeLayer === 3 && 'Injects Gaussian differential privacy noise and computes encrypted sum updates within hardware-isolated TEE enclaves.'}
                    {activeLayer === 4 && 'Aggregates multi-bank gradient weights using Trimmed-Mean to automatically neutralize malicious or corrupted node updates.'}
                    {activeLayer === 5 && 'Generates cryptographic sign-off hashes and exports automated SAR XML filings directly to regulatory SIEM endpoints.'}
                  </p>
                </div>

                <div className="p-4 rounded-xl bg-slate-900 font-mono text-[11px] text-indigo-300 border border-slate-800 overflow-x-auto">
                  {activeLayer === 1 && `<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pacs.008.001.08">
  <FIToFICstmrCdtTrf>
    <GrpHdr><MsgId>JPM-2026-9912</MsgId></GrpHdr>
    <CdtTrfTxInf><IntrBkSttlmAmt Ccy="USD">1450000.00</IntrBkSttlmAmt></CdtTrfTxInf>
  </FIToFICstmrCdtTrf>
</Document>`}
                  {activeLayer === 2 && `import torch_geometric as pyg
edge_index = pyg.data.Data(x=nodes, edge_index=graph_topology)
gat_layer = PyG.GATConv(in_channels=512, out_channels=256)`}
                  {activeLayer === 3 && `// Intel SGX Hardware Enclave Call
sgx_status_t status = ecall_aggregate_encrypted_weights(
    eid, &retval, ciphertext_a, ciphertext_b, noise_sigma
);`}
                  {activeLayer === 4 && `def trimmed_mean_fedavg(weight_tensors, beta=0.1):
    sorted_weights = torch.sort(weight_tensors, dim=0)
    return torch.mean(sorted_weights[beta:-beta], dim=0)`}
                  {activeLayer === 5 && `<FinCEN_SAR_Export version="2.0">
  <FilingHeader><FilerID>CFI-PLATFORM-991</FilerID></FilingHeader>
  <SuspiciousActivity><Amount Ccy="USD">1450000.00</Amount></SuspiciousActivity>
</FinCEN_SAR_Export>`}
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-indigo-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-indigo-500 to-purple-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 7: ADVERSARIAL ATTACK SIMULATION LAB ────────── */}
        <motion.section
          id="security"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-emerald-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            <div className="p-3.5 rounded-2xl bg-emerald-950/80 border border-emerald-500/30 font-mono text-xs text-emerald-200">
              <span className="font-bold text-emerald-400 block mb-1">PROJECT ARCHITECTURE & IMPACT:</span>
              Simulates security verification against adversarial vectors (MIA, DLG, Byzantine poisoning), proving zero raw data extraction risks.
            </div>

            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <span className="text-xs font-mono font-bold text-emerald-400 uppercase tracking-wider">
                  ADVERSARIAL ATTACK DEFENSE LAB
                </span>
                <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100 mt-1">
                  Zero-Trust Attack & Privacy Leakage Simulator
                </h2>
                <p className="text-xs text-slate-400 mt-1">
                  Simulate state-of-the-art adversarial AI attacks and verify zero-trust defenses.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => handleRunAttack('mia')}
                  className={`px-3.5 py-2 rounded-xl text-xs font-bold shadow-md transition-all ${
                    attackType === 'mia' ? 'bg-indigo-600 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  Launch MIA Attack 🎯
                </button>
                <button
                  onClick={() => handleRunAttack('dlg')}
                  className={`px-3.5 py-2 rounded-xl text-xs font-bold shadow-md transition-all ${
                    attackType === 'dlg' ? 'bg-purple-600 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  Launch DLG Attack 🔓
                </button>
                <button
                  onClick={() => handleRunAttack('byzantine')}
                  className={`px-3.5 py-2 rounded-xl text-xs font-bold shadow-md transition-all ${
                    attackType === 'byzantine' ? 'bg-rose-600 text-white' : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  Toggle Byzantine ☣️
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
              <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800 space-y-3 font-mono">
                <span className="text-xs text-slate-400 uppercase font-bold">Attack Target Detail</span>
                <div className="text-sm font-bold text-slate-200">
                  {attackType === 'mia' && 'Membership Inference Attack (MIA)'}
                  {attackType === 'dlg' && 'Deep Leakage from Gradients (DLG)'}
                  {attackType === 'byzantine' && 'Byzantine Gradient Poisoning'}
                </div>
                <p className="text-xs text-slate-400 leading-relaxed">
                  {attackType === 'mia' && 'Attempts to infer whether a specific customer transaction was present in local bank training sets.'}
                  {attackType === 'dlg' && 'Uses dummy inputs and gradient matching optimization to reconstruct raw customer data tensors.'}
                  {attackType === 'byzantine' && 'Injects malicious weight updates to force global GNN model accuracy degradation.'}
                </p>
              </div>

              <div className="p-6 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between font-mono">
                <span className="text-xs text-slate-400 uppercase font-bold">Zero-Trust Defense Status</span>
                <div className="text-base font-bold text-emerald-400 mt-2">
                  {attackStatus}
                </div>
                <div className="mt-4 p-3 rounded-xl bg-slate-900 text-[11px] text-slate-300 border border-slate-800">
                  Defense Mechanism: Intel SGX TEE + Trimmed-Mean Aggregation + Gaussian DP Noise
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-purple-500 to-emerald-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 8: LIVE REST API EXECUTION & MULTI-LANG SDK STUDIO ── */}
        <motion.section
          id="api"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-indigo-500/20 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            <div className="p-3.5 rounded-2xl bg-indigo-950/80 border border-indigo-500/30 font-mono text-xs text-indigo-200">
              <span className="font-bold text-indigo-400 block mb-1">PROJECT ARCHITECTURE & IMPACT:</span>
              Enables bank software engineering teams to integrate the coordinator platform into existing core banking infrastructure using 3 lines of code.
            </div>

            <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 pb-6 border-b border-slate-800">
              <div>
                <div className="flex items-center gap-2">
                  <CodeIcon />
                  <h2 className="text-xl sm:text-2xl font-extrabold text-slate-100">Live REST API & Multi-Lang SDK Studio</h2>
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  Test live REST endpoints and generate production client code in cURL, Python, Node.js, and Go.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <select
                  value={apiEndpoint}
                  onChange={(e) => setApiEndpoint(e.target.value)}
                  className="p-2 rounded-xl bg-slate-950 border border-slate-800 text-xs font-mono text-indigo-300 font-bold"
                >
                  <option value="handshake">POST /api/v1/coordinator/handshake</option>
                  <option value="clients">GET /api/v1/coordinator/clients</option>
                  <option value="abac">POST /api/v1/security/abac/evaluate</option>
                </select>

                <button
                  onClick={handleSendApiRequest}
                  disabled={isApiLoading}
                  className="px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold shadow-md transition-all flex items-center gap-1.5 flex-shrink-0"
                >
                  {isApiLoading ? 'Executing...' : 'Execute Test 🚀'}
                </button>
              </div>
            </div>

            <div className="flex gap-2 pt-4">
              {(['curl', 'python', 'node', 'go'] as const).map((lang) => (
                <button
                  key={lang}
                  onClick={() => setApiLang(lang)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold uppercase transition-all ${
                    apiLang === lang ? 'bg-indigo-600 text-white' : 'bg-slate-900 border border-slate-800 text-slate-400'
                  }`}
                >
                  {lang}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-4">
              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">Production Client Code ({apiLang})</span>
                <div className="w-full h-52 mt-2 p-4 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs text-indigo-300 overflow-y-auto">
                  <pre>{getSdkCode()}</pre>
                </div>
              </div>

              <div>
                <span className="text-xs font-mono font-bold text-slate-400 uppercase">HTTP 200 JSON Response & Headers</span>
                <div className="w-full h-52 mt-2 p-4 rounded-2xl bg-slate-950 border border-slate-800 font-mono text-xs text-emerald-400 overflow-y-auto">
                  {apiResponse ? (
                    <pre>{`HTTP/1.1 200 OK
X-Privacy-Budget-Remaining: 0.50
X-Enclave-Signature: ed25519_992f1b4a...

${apiResponse}`}</pre>
                  ) : (
                    <span className="text-slate-600">// Click "Execute Test" to receive live response</span>
                  )}
                </div>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── ANIMATED LASER DIVIDER BEAM ─────────────────────────── */}
        <div className="relative w-full max-w-7xl mx-auto h-px my-4 bg-gradient-to-r from-transparent via-emerald-500/50 to-transparent">
          <div className="absolute inset-0 bg-gradient-to-r from-emerald-500 to-indigo-500 opacity-75 blur-sm" />
        </div>

        {/* ── SECTION 9: CUSTOMIZED DEPLOYMENT WIZARD ────────────── */}
        <motion.section
          id="docs"
          initial={{ opacity: 0, y: 30 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: false }}
          transition={{ duration: 0.6 }}
          className="py-12 sm:py-16 px-4 sm:px-6 max-w-7xl mx-auto space-y-8"
        >
          <div className="glass-card border border-slate-800 rounded-3xl bg-slate-900/50 p-6 sm:p-10 backdrop-blur-xl space-y-6">
            <div className="p-3.5 rounded-2xl bg-emerald-950/80 border border-emerald-500/30 font-mono text-xs text-emerald-200">
              <span className="font-bold text-emerald-400 block mb-1">PROJECT ARCHITECTURE & IMPACT:</span>
              Provides turn-key infrastructure templates (Kubernetes Helm, Docker Compose, Terraform) for member banks to initialize local nodes within 5 minutes.
            </div>

            <div className="text-center max-w-3xl mx-auto space-y-3 mb-6">
              <span className="px-3.5 py-1.5 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-xs font-bold uppercase tracking-wider">
                DEPLOYMENT BLUEPRINT WIZARD
              </span>
              <h2 className="text-2xl sm:text-3xl font-extrabold text-slate-100">
                Generate Production Infrastructure Blueprint
              </h2>
              <p className="text-xs text-slate-400">
                Configure customized deployment templates for Kubernetes, Docker Compose, Terraform, or bare metal shell scripts.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
              <div>
                <label className="text-xs font-bold text-slate-300 uppercase">Target OS</label>
                <select
                  value={wizOs}
                  onChange={(e) => setWizOs(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200"
                >
                  <option value="linux">Linux Ubuntu 22.04 LTS</option>
                  <option value="macos">macOS Sonoma (Apple Silicon)</option>
                  <option value="windows">Windows Server Docker</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-bold text-slate-300 uppercase">Hardware Acceleration</label>
                <select
                  value={wizHardware}
                  onChange={(e) => setWizHardware(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200"
                >
                  <option value="cuda">NVIDIA CUDA 12.2 (Tensor Cores)</option>
                  <option value="metal">Apple Metal MPS</option>
                  <option value="cpu">CPU Only Monolith</option>
                </select>
              </div>

              <div>
                <label className="text-xs font-bold text-slate-300 uppercase">Compliance Standard</label>
                <select
                  value={wizRegulation}
                  onChange={(e) => setWizRegulation(e.target.value as any)}
                  className="w-full mt-1.5 p-3 rounded-xl bg-slate-950 border border-slate-800 text-xs font-semibold text-slate-200"
                >
                  <option value="eu_ai_act">EU AI Act High-Risk AML Tier</option>
                  <option value="ffiec">US FFIEC Compliance</option>
                  <option value="fca">UK FCA Regulatory Sandbox</option>
                </select>
              </div>
            </div>

            <div className="flex gap-2 mb-3">
              {(['helm', 'docker', 'terraform', 'shell'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setDeployTab(tab)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-mono font-bold uppercase transition-all ${
                    deployTab === tab ? 'bg-emerald-600 text-white' : 'bg-slate-950 border border-slate-800 text-slate-400'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>

            <div className="p-4 rounded-2xl bg-slate-950 border border-slate-800 flex flex-col justify-between font-mono text-xs text-emerald-400 space-y-3">
              <pre className="overflow-x-auto text-[11px] leading-relaxed text-indigo-300">{getDeployBlueprint()}</pre>
              <div className="flex justify-end pt-2 border-t border-slate-900">
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(getDeployBlueprint());
                    setCopiedWizCmd(true);
                    setTimeout(() => setCopiedWizCmd(false), 2000);
                  }}
                  className="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-bold flex items-center gap-1.5"
                >
                  {copiedWizCmd ? 'Copied! ✓' : 'Copy Blueprint'}
                </button>
              </div>
            </div>
          </div>
        </motion.section>

        {/* ── SECTION 10: ENTERPRISE FOOTER ────────────────────────── */}
        <footer className="border-t border-slate-800 bg-slate-950/90 py-12 sm:py-16 px-4 sm:px-6">
          <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-8">
            <div className="space-y-2 text-center md:text-left">
              <div className="flex items-center justify-center md:justify-start gap-2.5">
                <CfiLogoMark className="w-8 h-8" />
                <span className="font-extrabold text-base sm:text-lg text-slate-100">Collaborative Fraud Intelligence Platform</span>
              </div>
              <p className="text-xs text-slate-400">
                Privacy-Preserving Cross-Bank Federated Learning Infrastructure.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-4">
              <button
                onClick={() => navigate('/dashboard')}
                className="px-6 py-3 rounded-xl bg-gradient-to-r from-indigo-500 to-purple-600 text-white font-bold text-xs shadow-lg shadow-indigo-500/20 hover:scale-105 transition-all"
              >
                Launch Live Platform Demo →
              </button>
            </div>
          </div>

          <div className="max-w-7xl mx-auto mt-12 pt-6 border-t border-slate-900 text-center text-xs text-slate-600">
            © {new Date().getFullYear()} CFI Platform. All Rights Reserved. Built for EU AI Act & GDPR Article 25 Compliance.
          </div>
        </footer>
      </div>
    </div>
  );
}
