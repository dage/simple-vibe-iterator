import asyncio
import base64
from pathlib import Path

from src.chrome_devtools_service import ChromeDevToolsService

HTML = """<!DOCTYPE html>
<html lang=\"en\">
<head>
    <meta charset=\"UTF-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">
    <title>Hello World</title>
    <script type=\"importmap\">
        {
            \"imports\": {
                \"three\": \"https://unpkg.com/three@0.158.0/build/three.module.js\",
                \"three/addons/\": \"https://unpkg.com/three@0.158.0/examples/jsm/\"
            }
        }
    </script>
    <style>
        body {
            margin: 0;
            padding: 0;
            overflow: hidden;
            background: linear-gradient(to bottom, #f0f8ff, #e6e6fa);
        }
    </style>
</head>
<body>
    <script type=\"module\">
        document.addEventListener('DOMContentLoaded', function() {
            console.log('🚀 DOM Ready event fired!');
            document.body.style.border = '10px solid #ff0000';
            document.body.style.boxSizing = 'border-box';
            setTimeout(() => { document.body.style.border = '10px solid #00ff00'; }, 500);
            setTimeout(() => { document.body.style.border = '10px solid #0000ff'; }, 1000);
        });
        console.log('📜 Script started executing');
        window.addEventListener('load', function() {
            console.log('✅ Window fully loaded (all assets)');
            document.body.style.borderColor = '#ffff00';
        });
        import * as THREE from 'three';
        import { FontLoader } from 'three/addons/loaders/FontLoader.js';
        import { TextGeometry } from 'three/addons/geometries/TextGeometry.js';
        console.log('Three.js imported successfully');
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0xf0f8ff);
        const camera = new THREE.PerspectiveCamera(75, window.innerWidth / window.innerHeight, 0.1, 1000);
        camera.position.set(0, 0, 50);
        const renderer = new THREE.WebGLRenderer({ antialias: true });
        renderer.setSize(window.innerWidth, window.innerHeight);
        renderer.setClearColor(0xf0f8ff);
        renderer.shadowMap.enabled = true;
        renderer.shadowMap.type = THREE.PCFSoftShadowMap;
        document.body.appendChild(renderer.domElement);
        console.log('Renderer created and appended');
        const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
        scene.add(ambientLight);
        const directionalLight = new THREE.DirectionalLight(0xffffff, 0.8);
        directionalLight.position.set(10, 10, 5);
        directionalLight.castShadow = true;
        directionalLight.shadow.mapSize.width = 2048;
        directionalLight.shadow.mapSize.height = 2048;
        directionalLight.shadow.camera.near = 0.5;
        directionalLight.shadow.camera.far = 50;
        directionalLight.shadow.camera.left = -50;
        directionalLight.shadow.camera.right = 50;
        directionalLight.shadow.camera.top = 50;
        directionalLight.shadow.camera.bottom = -50;
        scene.add(directionalLight);
        console.log('Lights added');
        const groundGeometry = new THREE.PlaneGeometry(200, 200);
        const groundMaterial = new THREE.MeshLambertMaterial({ color: 0x87ceeb, transparent: true, opacity: 0.5 });
        const ground = new THREE.Mesh(groundGeometry, groundMaterial);
        ground.rotation.x = -Math.PI / 2;
        ground.position.y = -20;
        ground.receiveShadow = true;
        scene.add(ground);
        console.log('Ground plane added');
        let textMesh, outlineMesh;
        let fontLoaded = false;
        const loader = new FontLoader();
        loader.load(
            'https://unpkg.com/three@0.158.0/examples/fonts/helvetiker_regular.typeface.json', 
            function (font) {
                console.log('Font loaded successfully');
                fontLoaded = true;
                const textGeometry = new TextGeometry('Hello World', {
                    font: font,
                    size: 8,
                    height: 2,
                    curveSegments: 12,
                    bevelEnabled: true,
                    bevelThickness: 0.5,
                    bevelSize: 0.3,
                    bevelOffset: 0,
                    bevelSegments: 5
                });
                textGeometry.computeBoundingBox();
                const centerOffsetX = -0.5 * (textGeometry.boundingBox.max.x - textGeometry.boundingBox.min.x);
                textGeometry.translate(centerOffsetX, 0, 0);
                const textMaterial = new THREE.MeshPhongMaterial({ 
                    color: 0x4ecdc4,
                    shininess: 100,
                    transparent: true,
                    opacity: 0.9
                });
                textMesh = new THREE.Mesh(textGeometry, textMaterial);
                textMesh.position.y = -10;
                textMesh.castShadow = true;
                textMesh.receiveShadow = true;
                scene.add(textMesh);
                const outlineGeometry = new TextGeometry('Hello World', {
                    font: font,
                    size: 8.2,
                    height: 2.2,
                    curveSegments: 12,
                    bevelEnabled: true,
                    bevelThickness: 0.6,
                    bevelSize: 0.4
                });
                outlineGeometry.computeBoundingBox();
                outlineGeometry.translate(centerOffsetX, 0, 0);
                const outlineMaterial = new THREE.MeshPhongMaterial({ 
                    color: 0xff6b6b,
                    shininess: 10,
                    transparent: true,
                    opacity: 0.3
                });
                outlineMesh = new THREE.Mesh(outlineGeometry, outlineMaterial);
                outlineMesh.position.y = -10;
                scene.add(outlineMesh);
                console.log('Text meshes added');
            },
            function (progress) { console.log('Font loading progress:', progress); },
            function (error) {
                console.error('Font load error:', error);
                fontLoaded = false;
                const fallbackGeometry = new THREE.BoxGeometry(20, 2, 2);
                const fallbackMaterial = new THREE.MeshPhongMaterial({ color: 0x4ecdc4 });
                textMesh = new THREE.Mesh(fallbackGeometry, fallbackMaterial);
                textMesh.position.set(-5, -10, 0);
                scene.add(textMesh);
                console.log('Fallback box added');
            }
        );
        const particleCount = 200;
        const particles = new THREE.BufferGeometry();
        const positions = new Float32Array(particleCount * 3);
        const colors = new Float32Array(particleCount * 3);
        const sizes = new Float32Array(particleCount);
        for (let i = 0; i < particleCount; i++) {
            positions[i * 3] = (Math.random() - 0.5) * 100;
            positions[i * 3 + 1] = (Math.random() - 0.5) * 100;
            positions[i * 3 + 2] = (Math.random() - 0.5) * 100;
            colors[i * 3] = Math.random();
            colors[i * 3 + 1] = Math.random();
            colors[i * 3 + 2] = Math.random();
            sizes[i] = Math.random() * 5 + 1;
        }
        particles.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        particles.setAttribute('color', new THREE.BufferAttribute(colors, 3));
        particles.setAttribute('size', new THREE.BufferAttribute(sizes, 1));
        const particleMaterial = new THREE.PointsMaterial({
            size: 2,
            vertexColors: true,
            transparent: true,
            opacity: 0.8,
            blending: THREE.AdditiveBlending
        });
        const particleSystem = new THREE.Points(particles, particleMaterial);
        scene.add(particleSystem);
        console.log('Particle system added');
        let time = 0;
        const animateCamera = () => {
            time += 0.01;
            const radius = 40 + Math.sin(time) * 10;
            const phi = time * 0.5;
            const theta = Math.sin(time * 0.3) * Math.PI * 0.5;
            camera.position.x = radius * Math.sin(phi) * Math.cos(theta);
            camera.position.y = radius * Math.sin(theta) + 5;
            camera.position.z = radius * Math.cos(phi) * Math.cos(theta);
            const lookAt = new THREE.Vector3(0, -10, 0);
            camera.lookAt(lookAt);
        };
        function animate() {
            requestAnimationFrame(animate);
            animateCamera();
            if (textMesh) {
                textMesh.rotation.y += 0.005;
            }
            if (outlineMesh) {
                outlineMesh.rotation.y += 0.005;
            }
            const positions = particleSystem.geometry.attributes.position.array;
            for (let i = 0; i < particleCount; i++) {
                positions[i * 3 + 1] += Math.sin(time + i) * 0.01;
                positions[i * 3 + 2] += Math.cos(time + i) * 0.01;
            }
            particleSystem.geometry.attributes.position.needsUpdate = true;
            particleSystem.rotation.y += 0.002;
            renderer.render(scene, camera);
        }
        console.log('Starting animation loop');
        animate();
        window.addEventListener('resize', () => {
            camera.aspect = window.innerWidth / window.innerHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(window.innerWidth, window.innerHeight);
        });
    </script>
</body>
</html>"""

async def main():
    service = ChromeDevToolsService()
    ok = await service.load_html_mcp(HTML)
    print("load ok", ok)
    data_url = await service.take_screenshot_mcp()
    if not data_url:
        print("no screenshot")
        return
    prefix = "data:image/png;base64,"
    assert data_url.startswith(prefix)
    img_bytes = base64.b64decode(data_url[len(prefix):])
    out_path = Path("artifacts/repro_three.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(img_bytes)
    print("wrote", out_path)
    await service.aclose()

if __name__ == "__main__":
    asyncio.run(main())
