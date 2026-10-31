import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js'

const ACCENT = '#ef5b2a'
const STATUS = { working: '#ef5b2a', waiting: '#e7ad4a', error: '#d54c3f', delivered: '#ef5b2a', done: '#7a9c88', queued: '#8d928a', idle: '#7a9c88', offline: '#8d928a' }
const tmpVector = new THREE.Vector3()
const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char])

function material(color, roughness = 0.68, metalness = 0.04) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness })
}
function mesh(geometry, mat, parent, position = [0, 0, 0]) {
  const object = new THREE.Mesh(geometry, mat)
  object.position.set(...position)
  object.castShadow = true
  object.receiveShadow = true
  parent.add(object)
  return object
}
function box(parent, size, position, color, radius = 0.06) {
  return mesh(new RoundedBoxGeometry(...size, 3, radius), material(color), parent, position)
}
function cylinder(parent, radius, height, position, color, segments = 32) {
  return mesh(new THREE.CylinderGeometry(radius, radius, height, segments), material(color), parent, position)
}
function markSelectable(group, type, id) {
  group.userData.selectable = { type, id }
  group.traverse((child) => { child.userData.selectable = { type, id } })
}
function grassTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 256
  const ctx = canvas.getContext('2d')
  ctx.fillStyle = '#a8b89e'
  ctx.fillRect(0, 0, 256, 256)
  let seed = 19
  const random = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646 }
  for (let i = 0; i < 1500; i += 1) {
    const light = random() > 0.55
    ctx.fillStyle = light ? 'rgba(238,239,210,.13)' : 'rgba(49,76,52,.10)'
    ctx.fillRect(random() * 256, random() * 256, 1 + random() * 2, 1 + random() * 3)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping
  texture.repeat.set(6, 4)
  texture.colorSpace = THREE.SRGBColorSpace
  return texture
}
function screenTexture() {
  const canvas = document.createElement('canvas')
  canvas.width = 1024
  canvas.height = 560
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  return { canvas, texture }
}

export function createWorld(canvas, labelRoot, callbacks = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 0.92
  renderer.outputColorSpace = THREE.SRGBColorSpace

  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#e9e7df')
  scene.fog = new THREE.Fog('#e9e7df', 26, 52)
  const camera = new THREE.OrthographicCamera(-10, 10, 7, -7, 0.1, 100)
  camera.position.set(14, 13, 16)
  const controls = new OrbitControls(camera, canvas)
  controls.target.set(0, 0.3, 0)
  controls.enableDamping = true
  controls.dampingFactor = 0.065
  controls.minZoom = 0.65
  controls.maxZoom = 2.8
  controls.maxPolarAngle = Math.PI * 0.46
  controls.minPolarAngle = Math.PI * 0.17
  controls.autoRotate = true
  controls.autoRotateSpeed = 0.28
  controls.zoomSpeed = 0.75
  controls.panSpeed = 0.65

  scene.add(new THREE.HemisphereLight('#fffdf3', '#75806f', 1.45))
  const sun = new THREE.DirectionalLight('#fff7e6', 2.8)
  sun.position.set(-8, 18, 11)
  sun.castShadow = true
  sun.shadow.mapSize.set(2048, 2048)
  sun.shadow.camera.left = -15
  sun.shadow.camera.right = 15
  sun.shadow.camera.top = 15
  sun.shadow.camera.bottom = -15
  sun.shadow.bias = -0.00035
  sun.shadow.normalBias = 0.035
  scene.add(sun)
  const rim = new THREE.DirectionalLight('#e5efff', 1.1)
  rim.position.set(10, 7, -10)
  scene.add(rim)

  const ground = mesh(new THREE.PlaneGeometry(180, 180), material('#dedbd1', 0.96), scene, [0, -0.42, 0])
  ground.rotation.x = -Math.PI / 2
  ground.castShadow = false
  const island = new THREE.Group()
  scene.add(island)
  box(island, [15.4, 0.58, 10.7], [0, -0.11, 0], '#77836f', 0.42)
  const turf = box(island, [15.25, 0.36, 10.55], [0, 0.13, 0], '#a8b89e', 0.38)
  turf.material = new THREE.MeshStandardMaterial({ map: grassTexture(), roughness: 0.96, color: '#ffffff' })

  const grid = new THREE.GridHelper(15, 30, '#71816c', '#8e9e85')
  grid.position.y = 0.321
  grid.material.transparent = true
  grid.material.opacity = 0.11
  island.add(grid)

  const pathMat = material('#c8c8b5', 0.95)
  const addPath = (from, to, count) => {
    for (let i = 0; i < count; i += 1) {
      const t = i / Math.max(1, count - 1)
      const step = mesh(new RoundedBoxGeometry(0.64, 0.055, 0.44, 2, 0.07), pathMat, island, [THREE.MathUtils.lerp(from[0], to[0], t), 0.35, THREE.MathUtils.lerp(from[1], to[1], t)])
      step.rotation.y = Math.atan2(to[0] - from[0], to[1] - from[1])
      step.castShadow = false
    }
  }
  addPath([-2.2, 0.6], [-0.6, -2.8], 7)
  addPath([2.7, -0.2], [0.8, -2.8], 6)
  addPath([-1.0, 1.5], [1.6, 0.4], 5)

  const projectObjects = new Map()
  const projectLabels = new Map()
  const agentObjects = new Map()
  const agentLabels = new Map()
  const linkGroup = new THREE.Group()
  scene.add(linkGroup)
  const ambience = new THREE.Group()
  scene.add(ambience)
  const selectable = []

  function htmlLabel(className, html, onClick) {
    const element = document.createElement('div')
    element.className = className
    element.innerHTML = html
    element.addEventListener('click', (event) => { event.stopPropagation(); onClick?.() })
    labelRoot.appendChild(element)
    return element
  }
  function positionLabel(element, point, yOffset = 0) {
    tmpVector.copy(point).project(camera)
    const x = (tmpVector.x * 0.5 + 0.5) * canvas.clientWidth
    const y = (-tmpVector.y * 0.5 + 0.5) * canvas.clientHeight + yOffset
    element.style.transform = `translate(${x}px, ${y}px) translate(-50%, -100%)`
    element.style.visibility = tmpVector.z > 1 || x < -100 || x > canvas.clientWidth + 100 || y < -100 || y > canvas.clientHeight + 100 ? 'hidden' : 'visible'
  }

  function buildRobot(agent) {
    const group = new THREE.Group()
    group.position.set(...agent.position)
    scene.add(group)
    const visual = new THREE.Group()
    visual.scale.setScalar(0.78)
    group.add(visual)
    const baseRing = mesh(new THREE.TorusGeometry(0.56, 0.025, 8, 48), new THREE.MeshBasicMaterial({ color: STATUS[agent.status] || ACCENT, transparent: true, opacity: 0.68 }), group, [0, 0.37, 0])
    baseRing.rotation.x = Math.PI / 2
    const leftFoot = box(visual, [0.28, 0.18, 0.42], [-0.24, 0.19, 0.08], '#353934', 0.08)
    const rightFoot = box(visual, [0.28, 0.18, 0.42], [0.24, 0.19, 0.08], '#353934', 0.08)
    const leftLeg = cylinder(visual, 0.11, 0.46, [-0.23, 0.47, 0], '#62675f')
    const rightLeg = cylinder(visual, 0.11, 0.46, [0.23, 0.47, 0], '#62675f')
    const body = mesh(new THREE.CapsuleGeometry(0.46, 0.48, 6, 16), material('#f2eee3', 0.52), visual, [0, 0.97, 0])
    body.scale.z = 0.82
    box(visual, [0.55, 0.27, 0.08], [0, 1.01, 0.42], agent.color, 0.06)
    const leftArm = mesh(new THREE.CapsuleGeometry(0.11, 0.42, 4, 10), material(agent.color), visual, [-0.55, 0.99, 0])
    const rightArm = mesh(new THREE.CapsuleGeometry(0.11, 0.42, 4, 10), material(agent.color), visual, [0.55, 0.99, 0])
    leftArm.rotation.z = -0.16
    rightArm.rotation.z = 0.16
    const head = box(visual, [1.02, 0.72, 0.78], [0, 1.65, 0], '#f8f4e9', 0.2)
    box(visual, [0.81, 0.38, 0.07], [0, 1.66, 0.4], '#252a27', 0.11)
    const eyeMat = new THREE.MeshStandardMaterial({ color: agent.color, emissive: agent.color, emissiveIntensity: 1.7, roughness: 0.4 })
    mesh(new RoundedBoxGeometry(0.11, 0.08, 0.035, 2, 0.025), eyeMat, visual, [-0.19, 1.68, 0.45])
    mesh(new RoundedBoxGeometry(0.11, 0.08, 0.035, 2, 0.025), eyeMat, visual, [0.19, 1.68, 0.45])
    cylinder(visual, 0.035, 0.27, [0, 2.13, 0], '#4d524c', 12)
    const antenna = mesh(new THREE.SphereGeometry(0.09, 16, 12), eyeMat, visual, [0, 2.29, 0])
    markSelectable(group, 'agent', agent.id)
    selectable.push(group)
    const nameLabel = htmlLabel('agent-label', '', () => callbacks.onSelect?.('agent', agent.id))
    const taskLabel = htmlLabel('task-label', '', () => {})
    agentObjects.set(agent.id, { group, visual, body, head, leftArm, rightArm, leftLeg, rightLeg, leftFoot, rightFoot, baseRing, antenna, base: new THREE.Vector3(...agent.position), target: null, pulse: 0, bubbleUntil: 0, currentTasks: '', currentStatus: '' })
    agentLabels.set(agent.id, { nameLabel, taskLabel })
  }

  const pool = new THREE.Group()
  pool.position.set(0, 0, -3.75)
  scene.add(pool)
  box(pool, [4.3, 0.22, 0.65], [0, 0.42, 0], '#3d433b', 0.12)
  box(pool, [0.19, 1.55, 0.19], [-1.6, 1.15, 0], '#3d433b', 0.05)
  box(pool, [0.19, 1.55, 0.19], [1.6, 1.15, 0], '#3d433b', 0.05)
  box(pool, [4.45, 2.35, 0.24], [0, 2.25, 0], '#262b27', 0.15)
  box(pool, [4.16, 2.05, 0.05], [0, 2.25, 0.145], '#171c19', 0.08)
  const screen = screenTexture()
  const screenMesh = mesh(new THREE.PlaneGeometry(4.03, 1.92), new THREE.MeshBasicMaterial({ map: screen.texture }), pool, [0, 2.25, 0.177])
  screenMesh.castShadow = false
  box(pool, [0.7, 0.09, 0.09], [0, 3.48, 0], ACCENT, 0.035)
  markSelectable(pool, 'pool', 'main')
  selectable.push(pool)
  const poolLabel = htmlLabel('pool-label', 'TASK POOL <span>↗</span>', () => callbacks.onSelect?.('pool', 'main'))

  function drawPool(snapshot) {
    const ctx = screen.canvas.getContext('2d')
    ctx.fillStyle = '#18201b'
    ctx.fillRect(0, 0, 1024, 560)
    ctx.fillStyle = '#ef5b2a'
    ctx.fillRect(44, 42, 10, 54)
    ctx.font = '600 31px Arial, sans-serif'
    ctx.fillStyle = '#f1eee3'
    ctx.fillText('TASK POOL', 78, 76)
    ctx.font = '18px monospace'
    ctx.fillStyle = '#8f9b90'
    ctx.fillText(`${snapshot.tasks.filter((task) => task.status !== 'done').length.toString().padStart(2, '0')} OPEN / LIVE QUEUE`, 760, 72)
    const visible = [...snapshot.tasks].sort((a, b) => {
      const rank = { queued: 0, error: 1, waiting: 2, delivered: 3, working: 4, done: 5 }
      return rank[a.status] - rank[b.status]
    }).slice(0, 4)
    visible.forEach((task, index) => {
      const y = 135 + index * 91
      ctx.fillStyle = index % 2 ? '#202922' : '#1d251f'
      ctx.fillRect(40, y - 33, 944, 74)
      ctx.fillStyle = STATUS[task.status] || '#ef5b2a'
      ctx.beginPath(); ctx.arc(72, y + 2, 7, 0, Math.PI * 2); ctx.fill()
      ctx.font = '20px monospace'
      ctx.fillStyle = '#8f9b90'
      ctx.fillText(task.id, 104, y + 9)
      ctx.font = '24px Arial, sans-serif'
      ctx.fillStyle = '#eeeadd'
      ctx.fillText(task.title, 222, y + 9)
      ctx.font = '19px Arial, sans-serif'
      ctx.fillStyle = STATUS[task.status] || '#ef5b2a'
      ctx.fillText(task.short, 814, y + 9)
    })
    ctx.font = '16px monospace'
    ctx.fillStyle = '#657267'
    ctx.fillText('SELECT A TASK TO ENTER THE GROUP CHAT  →', 43, 524)
    screen.texture.needsUpdate = true
  }

  const cloudMaterial = material('#f5f2e9', 1)
  for (let c = 0; c < 3; c += 1) {
    const cloud = new THREE.Group()
    cloud.position.set(-13 + c * 11, 7 + c * 0.6, -9 - c * 4)
    for (let p = 0; p < 5; p += 1) {
      const puff = mesh(new THREE.SphereGeometry(0.75 + (p % 2) * 0.25, 18, 12), cloudMaterial, cloud, [p * 0.85, Math.sin(p) * 0.25, 0])
      puff.castShadow = false
    }
    cloud.scale.setScalar(0.8 + c * 0.12)
    cloud.userData.speed = 0.09 + c * 0.025
    ambience.add(cloud)
  }
  const birds = new THREE.Group()
  for (let b = 0; b < 5; b += 1) {
    const geometry = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-0.22, 0, 0), new THREE.Vector3(0, 0.13, 0), new THREE.Vector3(0.22, 0, 0)])
    const bird = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: '#59675d', transparent: true, opacity: 0.55 }))
    bird.position.set(b * 0.7, Math.sin(b) * 0.3, b * 0.35)
    birds.add(bird)
  }
  birds.position.set(-7, 5.5, -4)
  ambience.add(birds)

  const raycaster = new THREE.Raycaster()
  const pointer = new THREE.Vector2()
  let pointerStart = null
  canvas.addEventListener('pointerdown', (event) => { pointerStart = { x: event.clientX, y: event.clientY }; cameraGoal = null; callbacks.onInteract?.() })
  canvas.addEventListener('wheel', () => { cameraGoal = null; callbacks.onInteract?.() }, { passive: true })
  canvas.addEventListener('pointerup', (event) => {
    if (!pointerStart || Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y) > 6) return
    const rect = canvas.getBoundingClientRect()
    pointer.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1)
    raycaster.setFromCamera(pointer, camera)
    const hits = raycaster.intersectObjects(selectable, true)
    if (hits.length) {
      const target = hits[0].object.userData.selectable
      if (target) callbacks.onSelect?.(target.type, target.id)
    }
  })

  let snapshot = null
  const layers = { tasks: true, names: true, links: false, ambience: true }
  let cameraGoal = null
  let eventAnimations = []
  let lastPoolKey = ''
  let tourEnabled = true
  let idleTimer = null
  controls.addEventListener('start', () => {
    controls.autoRotate = false
    clearTimeout(idleTimer)
  })
  controls.addEventListener('end', () => {
    clearTimeout(idleTimer)
    idleTimer = setTimeout(() => { if (tourEnabled) controls.autoRotate = true }, 12000)
  })

  function sync(nextSnapshot) {
    nextSnapshot.projects = []
    nextSnapshot.agents.forEach((agent) => { if (!agentObjects.has(agent.id)) buildRobot(agent) })
    snapshot = nextSnapshot
    const poolKey = snapshot.tasks.map((task) => `${task.id}:${task.status}:${task.short}`).join('|')
    if (poolKey !== lastPoolKey) { drawPool(snapshot); lastPoolKey = poolKey }
    snapshot.projects.forEach((project) => {
      const object = projectObjects.get(project.id)
      if (object && object.outputs !== project.outputs) {
        const index = object.docs.children.length
        const doc = box(object.docs, [0.28, 0.06, 0.38], [-0.9 + (index % 4) * 0.28, 1.2 + Math.floor(index / 4) * 0.065, 0.49], ACCENT, 0.015)
        doc.rotation.y = 0.1
        object.outputs = project.outputs
      }
    })
    snapshot.agents.forEach((agent) => {
      const object = agentObjects.get(agent.id)
      const labels = agentLabels.get(agent.id)
      const agentTasks = snapshot.tasks.filter((task) => task.agent === agent.id && task.status !== 'done')
      const taskKey = agentTasks.map((task) => `${task.id}:${task.status}:${task.short}:${task.updated || 0}`).join('|')
      if (taskKey !== object.currentTasks) {
        const primary = [...agentTasks].sort((a, b) => (b.updated || 0) - (a.updated || 0))[0]
        labels.taskLabel.innerHTML = primary ? `<div class="task-bubble ${escapeHtml(primary.status)}" data-task="${escapeHtml(primary.id)}"><i></i><span><small>${escapeHtml(primary.id)} · LATEST</small><b>${escapeHtml(primary.short)}</b></span></div>` : ''
        object.currentTasks = taskKey
      }
      const statusKey = `${agent.status}:${agentTasks.length}`
      if (object.currentStatus !== statusKey) {
        labels.nameLabel.innerHTML = `<button class="agent-name"><i style="--status:${STATUS[agent.status]}"></i>${escapeHtml(agent.name)}<span>${agentTasks.length ? `${agentTasks.length} TASK${agentTasks.length > 1 ? 'S' : ''}` : ''}</span></button>`
        object.baseRing.material.color.set(STATUS[agent.status] || ACCENT)
        object.currentStatus = statusKey
      }
    })
    snapshot.projects.forEach((project) => {
      const activeTasks = snapshot.tasks.filter((task) => task.project === project.id && task.status !== 'done').length
      projectLabels.get(project.id).innerHTML = `<button><small>${project.subtitle}</small><b>${project.name}</b><span>${activeTasks} TASKS · ${project.outputs} OUTPUTS</span></button>`
      projectLabels.get(project.id).querySelector('button').onclick = () => callbacks.onSelect?.('project', project.id)
    })
    rebuildLinks()
  }

  function rebuildLinks() {
    linkGroup.clear()
    if (!snapshot) return
    snapshot.agents.filter((agent) => agent.project).forEach((agent) => {
      const project = snapshot.projects.find((item) => item.id === agent.project)
      const points = [new THREE.Vector3(project.position[0], 0.39, project.position[2]), new THREE.Vector3(agent.position[0], 0.39, agent.position[2])]
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(points), new THREE.LineDashedMaterial({ color: project.color, transparent: true, opacity: 0.34, dashSize: 0.16, gapSize: 0.13 }))
      line.computeLineDistances()
      linkGroup.add(line)
    })
    linkGroup.visible = layers.links
  }

  function playEvent(event) {
    const object = agentObjects.get(event.agent)
    if (object) {
      object.pulse = 1
      object.bubbleUntil = elapsed + 5.5
    }
    if (event.id === 'claim' && object) {
      object.target = new THREE.Vector3(0.9, 0, -2.3)
      setTimeout(() => { object.target = null }, 4500)
    }
  }

  function resize() {
    const width = canvas.clientWidth
    const height = canvas.clientHeight
    renderer.setSize(width, height, false)
    const aspect = width / height
    const size = width < 760 ? 17.8 : 9.2
    camera.left = -size * aspect
    camera.right = size * aspect
    camera.top = size
    camera.bottom = -size
    camera.updateProjectionMatrix()
  }
  window.addEventListener('resize', resize)
  resize()

  function setView(view) {
    const views = {
      overview: { position: [14, 13, 16], target: [0, 0.3, 0], zoom: 1 },
      agents: { position: [12, 15, 13], target: [0, 0.2, 0.8], zoom: 1.18 },
      pool: { position: [7, 7.5, 11], target: [0, 1.3, -3.1], zoom: 1.65 },
    }
    const next = views[view] || views.overview
    cameraGoal = { position: new THREE.Vector3(...next.position), target: new THREE.Vector3(...next.target), zoom: next.zoom }
  }
  function focus(type, id) {
    if (type === 'project' && projectObjects.has(id)) {
      const point = projectObjects.get(id).group.position
      cameraGoal = { position: point.clone().add(new THREE.Vector3(8, 9, 10)), target: point.clone().add(new THREE.Vector3(0, 0.7, 0)), zoom: 1.65 }
    } else if (type === 'agent' && agentObjects.has(id)) {
      const point = agentObjects.get(id).group.position
      cameraGoal = { position: point.clone().add(new THREE.Vector3(6, 7, 8)), target: point.clone().add(new THREE.Vector3(0, 0.9, 0)), zoom: 2.05 }
    } else if (type === 'pool') setView('pool')
  }

  const clock = new THREE.Clock()
  let elapsed = 0
  let stopped = false
  let animationFrame = 0
  function animate() {
    if (stopped) return
    const delta = Math.min(clock.getDelta(), 0.05)
    elapsed += delta
    if (cameraGoal) {
      const cameraEase = 1 - Math.exp(-delta * 5.5)
      camera.position.lerp(cameraGoal.position, cameraEase)
      controls.target.lerp(cameraGoal.target, cameraEase)
      camera.zoom = THREE.MathUtils.lerp(camera.zoom, cameraGoal.zoom, cameraEase)
      camera.updateProjectionMatrix()
      if (camera.position.distanceTo(cameraGoal.position) < 0.025 && Math.abs(camera.zoom - cameraGoal.zoom) < 0.005) cameraGoal = null
    }
    controls.update()
    if (snapshot) {
      snapshot.agents.forEach((agent, index) => {
        const object = agentObjects.get(agent.id)
        const base = object.target || object.base
        object.group.position.lerp(base, 0.025)
        object.visual.position.y = Math.sin(elapsed * 2 + index * 1.3) * (agent.status === 'idle' ? 0.012 : 0.035)
        const motion = agent.status === 'working' ? 0.22 : agent.status === 'error' ? 0.03 : 0.08
        object.leftArm.rotation.x = Math.sin(elapsed * 3.1 + index) * motion
        object.rightArm.rotation.x = -Math.sin(elapsed * 3.1 + index) * motion
        object.head.rotation.y = Math.sin(elapsed * 0.7 + index) * 0.09
        object.antenna.material.emissiveIntensity = agent.status === 'error' ? 1.2 + Math.sin(elapsed * 7) * 0.8 : 1.5
        object.baseRing.material.opacity = 0.45 + Math.sin(elapsed * 2.2 + index) * 0.16
        if (object.pulse > 0) {
          object.pulse = Math.max(0, object.pulse - delta * 0.55)
          const scale = 1 + Math.sin((1 - object.pulse) * 20) * object.pulse * 0.09
          object.visual.scale.setScalar(0.78 * scale)
        } else object.visual.scale.setScalar(0.78)
        const namePoint = object.group.position.clone().add(new THREE.Vector3(0, 1.93, 0))
        const taskPoint = object.group.position.clone().add(new THREE.Vector3(0, 2.95, 0))
        const labels = agentLabels.get(agent.id)
        positionLabel(labels.nameLabel, namePoint)
        positionLabel(labels.taskLabel, taskPoint)
        labels.nameLabel.style.display = layers.names ? '' : 'none'
        labels.taskLabel.style.display = layers.tasks && object.currentTasks ? '' : 'none'
        const bubblePhase = (elapsed + index * 1.65) % 9
        const bubbleVisible = object.bubbleUntil > elapsed || bubblePhase < 3.8
        labels.taskLabel.style.opacity = bubbleVisible ? '1' : '0'
      })
      snapshot.projects.forEach((project) => {
        const object = projectObjects.get(project.id)
        object.beacon.rotation.z += delta * 0.07
        positionLabel(projectLabels.get(project.id), object.group.position.clone().add(new THREE.Vector3(0, 0.9, 1.25)))
      })
      positionLabel(poolLabel, pool.position.clone().add(new THREE.Vector3(0, 3.65, 0)))
    }
    ambience.children.forEach((child) => {
      if (child.userData.speed) {
        child.position.x += child.userData.speed * delta
        if (child.position.x > 19) child.position.x = -20
      }
    })
    birds.position.x += delta * 0.24
    birds.position.y = 5.5 + Math.sin(elapsed * 0.5) * 0.25
    birds.children.forEach((bird, index) => { bird.scale.y = 0.6 + Math.sin(elapsed * 4 + index) * 0.4 })
    if (birds.position.x > 13) birds.position.x = -13
    eventAnimations = eventAnimations.filter((animation) => {
      animation.elapsed += delta
      const t = Math.min(1, animation.elapsed / animation.duration)
      animation.object.position.lerpVectors(animation.from, animation.to, t)
      animation.object.position.y += Math.sin(t * Math.PI) * 0.035
      animation.object.rotation.x += delta * 3
      animation.object.rotation.y += delta * 4
      animation.object.scale.setScalar(1 - t * 0.45)
      if (t >= 1) { scene.remove(animation.object); return false }
      return true
    })
    renderer.render(scene, camera)
    animationFrame = requestAnimationFrame(animate)
  }

  animate()
  return {
    dispose() {
      stopped = true
      cancelAnimationFrame(animationFrame)
      clearTimeout(idleTimer)
      window.removeEventListener('resize', resize)
      controls.dispose()
      scene.traverse((object) => {
        object.geometry?.dispose?.()
        const materials = object.material ? (Array.isArray(object.material) ? object.material : [object.material]) : []
        materials.forEach((item) => item.dispose?.())
      })
      renderer.dispose()
      labelRoot.replaceChildren()
    },
    sync,
    playEvent,
    setView,
    focus,
    resize,
    setLayer(name, visible) {
      layers[name] = visible
      if (name === 'links') linkGroup.visible = visible
      if (name === 'ambience') ambience.visible = visible
    },
    setTour(enabled) {
      tourEnabled = enabled
      controls.autoRotate = enabled
      if (!enabled) clearTimeout(idleTimer)
    },
    pauseTour() { controls.autoRotate = false },
    resumeTour() { if (tourEnabled) controls.autoRotate = true },
    projectSelectable(type, id) {
      const object = type === 'agent' ? agentObjects.get(id)?.group : type === 'project' ? projectObjects.get(id)?.group : pool
      if (!object) return null
      const point = object.position.clone().add(new THREE.Vector3(0, type === 'pool' ? 2 : 1, 0)).project(camera)
      const rect = canvas.getBoundingClientRect()
      return { x: rect.left + (point.x * 0.5 + 0.5) * rect.width, y: rect.top + (-point.y * 0.5 + 0.5) * rect.height }
    },
    getStats() { return { agents: agentObjects.size, projects: projectObjects.size, calls: renderer.info.render.calls, triangles: renderer.info.render.triangles, autoRotate: controls.autoRotate, zoom: camera.zoom } },
  }
}
