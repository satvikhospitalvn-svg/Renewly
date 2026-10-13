const glassShader = /* wgsl */ `
struct SurfaceInfo {
  resolution: vec2<f32>,
  time: f32,
  radius: f32,
};

@group(0) @binding(0) var<uniform> surface: SurfaceInfo;

struct VertexOutput {
  @builtin(position) position: vec4<f32>,
  @location(0) uv: vec2<f32>,
};

@vertex
fn vertexMain(@builtin(vertex_index) index: u32) -> VertexOutput {
  let points = array<vec2<f32>, 6>(
    vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0), vec2<f32>(1.0, 1.0),
    vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 1.0), vec2<f32>(0.0, 1.0),
  );
  let uv = points[index];
  var output: VertexOutput;
  output.position = vec4<f32>(uv * 2.0 - vec2<f32>(1.0, 1.0), 0.0, 1.0);
  output.uv = uv;
  return output;
}

@fragment
fn fragmentMain(input: VertexOutput) -> @location(0) vec4<f32> {
  let halfSize = surface.resolution * 0.5;
  let point = (input.uv - vec2<f32>(0.5, 0.5)) * surface.resolution;
  let radius = min(surface.radius, min(halfSize.x, halfSize.y));
  let q = abs(point) - (halfSize - vec2<f32>(radius, radius));
  let distance = length(max(q, vec2<f32>(0.0, 0.0)))
    + min(max(q.x, q.y), 0.0) - radius;

  let edge = exp(-pow(distance / 2.8, 2.0));
  let innerBevel = exp(-pow((distance + 5.0) / 3.0, 2.0));
  let lightDirection = clamp(1.0 - input.uv.x * 0.34 - input.uv.y * 0.48, 0.18, 1.0);
  let upperGlint = exp(-pow((input.uv.x - 0.2) / 0.23, 2.0)
    - pow((input.uv.y - 0.08) / 0.1, 2.0));
  let sweepPosition = 0.12 + 0.76 * (0.5 + 0.5 * sin(surface.time * 0.16));
  let movingSpecular = exp(-pow((input.uv.x - sweepPosition) / 0.12, 2.0)
    - pow((input.uv.y - 0.035) / 0.045, 2.0));

  let fill = 0.035 + 0.025 * lightDirection;
  let highlight = edge * (0.25 + 0.28 * lightDirection)
    + innerBevel * 0.12 + upperGlint * 0.17 + movingSpecular * 0.22;
  let alpha = min(0.62, fill + highlight);
  let tint = mix(vec3<f32>(0.36, 0.5, 0.64), vec3<f32>(0.94, 0.98, 1.0), lightDirection);

  // Premultiplied glass light keeps the original text and controls crisp above it.
  return vec4<f32>(tint * alpha, alpha);
}
`;

const surfaceSelector = [
  ".data-card", ".summary-card", ".nav-glass", ".renewal-item",
  ".feature-row", ".search-result", ".empty-state", ".toggle-row",
  ".search-field", ".clock", ".clock__block",
].join(",");

export async function initWebGpuGlass() {
  if (!navigator.gpu) return;

  let adapter;
  let device;
  let pipeline;
  const surfaces = new Map();
  let animationFrame = 0;
  let ready = false;
  let hasDrawn = false;
  let lastDraw = 0;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const resizeSurface = (record) => {
    if (!device || !record.element.isConnected) return;
    const rect = record.element.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 1.25);
    const width = Math.max(1, Math.round(rect.width * ratio));
    const height = Math.max(1, Math.round(rect.height * ratio));
    if (width === record.canvas.width && height === record.canvas.height) return;

    record.canvas.width = width;
    record.canvas.height = height;
    record.uniforms[0] = width;
    record.uniforms[1] = height;
    const radius = parseFloat(getComputedStyle(record.element).borderTopLeftRadius) || 16;
    record.uniforms[3] = Math.min(radius, rect.width * 0.5, rect.height * 0.5) * ratio;
    device.queue.writeBuffer(record.uniformBuffer, 0, record.uniforms);
  };

  const createSurface = (element) => {
    if (surfaces.has(element) || !element.isConnected) return;
    const canvas = document.createElement("canvas");
    canvas.className = "webgpu-glass-overlay";
    canvas.setAttribute("aria-hidden", "true");
    const context = canvas.getContext("webgpu");
    if (!context) return;

    const uniformBuffer = device.createBuffer({
      size: 16,
      usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
    });
    const uniforms = new Float32Array([1, 1, 0, 16]);
    context.configure({ device, format: navigator.gpu.getPreferredCanvasFormat(), alphaMode: "premultiplied" });
    element.classList.add("webgpu-glass-surface");
    element.insertBefore(canvas, element.firstChild);
    const record = {
      element,
      canvas,
      context,
      uniformBuffer,
      uniforms,
      bindGroup: device.createBindGroup({
        layout: pipeline.getBindGroupLayout(0),
        entries: [{ binding: 0, resource: { buffer: uniformBuffer } }],
      }),
    };
    surfaces.set(element, record);
    resizeObserver.observe(element);
    resizeSurface(record);
  };

  const scanSurfaces = () => {
    document.querySelectorAll(surfaceSelector).forEach(createSurface);
    for (const [element, record] of surfaces) {
      if (!element.isConnected) {
        record.context.unconfigure();
        record.uniformBuffer.destroy();
        surfaces.delete(element);
      }
    }
  };

  const draw = (timestamp) => {
    animationFrame = 0;
    if (!ready || document.hidden || (reducedMotion.matches && hasDrawn)) return;
    if (timestamp - lastDraw < 33) {
      animationFrame = window.requestAnimationFrame(draw);
      return;
    }
    lastDraw = timestamp;
    const encoder = device.createCommandEncoder();
    let hasVisibleSurface = false;

    for (const record of surfaces.values()) {
      if (!record.element.isConnected || !record.canvas.width || !record.canvas.height) continue;
      const rect = record.element.getBoundingClientRect();
      if (!rect.width || !rect.height || rect.bottom < 0 || rect.right < 0 || rect.top > innerHeight || rect.left > innerWidth) continue;
      hasVisibleSurface = true;
      record.uniforms[2] = timestamp / 1000;
      device.queue.writeBuffer(record.uniformBuffer, 0, record.uniforms);
      const pass = encoder.beginRenderPass({
        colorAttachments: [{
          view: record.context.getCurrentTexture().createView(),
          clearValue: { r: 0, g: 0, b: 0, a: 0 },
          loadOp: "clear",
          storeOp: "store",
        }],
      });
      pass.setPipeline(pipeline);
      pass.setBindGroup(0, record.bindGroup);
      pass.draw(6);
      pass.end();
    }

    if (hasVisibleSurface) device.queue.submit([encoder.finish()]);
    hasDrawn = true;
    if (!reducedMotion.matches) animationFrame = window.requestAnimationFrame(draw);
  };

  const syncAnimation = () => {
    const shouldAnimate = ready && !document.hidden && (!reducedMotion.matches || !hasDrawn);
    if (shouldAnimate && !animationFrame) animationFrame = window.requestAnimationFrame(draw);
    if (!shouldAnimate && animationFrame) {
      window.cancelAnimationFrame(animationFrame);
      animationFrame = 0;
    }
  };

  const resizeObserver = new ResizeObserver(() => {
    for (const record of surfaces.values()) resizeSurface(record);
  });
  const mutationObserver = new MutationObserver(() => {
    const previousCount = surfaces.size;
    scanSurfaces();
    if (surfaces.size !== previousCount) {
      hasDrawn = false;
      syncAnimation();
    }
  });
  document.addEventListener("visibilitychange", syncAnimation);
  reducedMotion.addEventListener("change", syncAnimation);

  try {
    adapter = await navigator.gpu.requestAdapter({ powerPreference: "low-power" });
    if (!adapter) return;
    device = await adapter.requestDevice();
    const format = navigator.gpu.getPreferredCanvasFormat();
    const module = device.createShaderModule({ code: glassShader });
    const compilation = await module.getCompilationInfo();
    if (compilation.messages.some((message) => message.type === "error")) {
      device.destroy();
      return;
    }

    pipeline = device.createRenderPipeline({
      layout: "auto",
      vertex: { module, entryPoint: "vertexMain" },
      fragment: {
        module,
        entryPoint: "fragmentMain",
        targets: [{
          format,
          blend: {
            color: { srcFactor: "one", dstFactor: "one-minus-src-alpha", operation: "add" },
            alpha: { srcFactor: "one", dstFactor: "one-minus-src-alpha", operation: "add" },
          },
        }],
      },
      primitive: { topology: "triangle-list" },
    });

    scanSurfaces();
    mutationObserver.observe(document.body, { childList: true, subtree: true });
    ready = true;
    syncAnimation();
    device.lost.then(() => {
      ready = false;
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
      mutationObserver.disconnect();
      resizeObserver.disconnect();
      document.removeEventListener("visibilitychange", syncAnimation);
      reducedMotion.removeEventListener("change", syncAnimation);
      for (const record of surfaces.values()) {
        record.context.unconfigure();
        record.uniformBuffer.destroy();
        record.canvas.remove();
        record.element.classList.remove("webgpu-glass-surface");
      }
      surfaces.clear();
    });
  } catch (error) {
    console.error("WebGPU glass initialization failed:", error);
    ready = false;
    mutationObserver.disconnect();
    resizeObserver.disconnect();
    document.removeEventListener("visibilitychange", syncAnimation);
    reducedMotion.removeEventListener("change", syncAnimation);
    for (const record of surfaces.values()) {
      record.context.unconfigure();
      record.uniformBuffer.destroy();
      record.canvas.remove();
      record.element.classList.remove("webgpu-glass-surface");
    }
    surfaces.clear();
    device?.destroy();
  }
}
