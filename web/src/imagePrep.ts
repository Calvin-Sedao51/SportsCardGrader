// Client-side image preparation: decode + downscale + re-encode to JPEG
// before upload. Camera files arrive at 4-12MB (often HEIC, which AI
// providers reject with a cryptic 502); re-encoding here makes uploads fast
// and universally accepted. Quality 0.85 at a 1600px long edge is chosen for
// vision-model legibility of card text; card corners and surface detail
// survive well at this size.

const MAX_LONG_EDGE = 1600
const JPEG_QUALITY = 0.85
// Already-small JPEGs gain nothing from a re-encode round trip.
const SKIP_RE_ENCODE_BYTES = 1024 * 1024

/** Scale so the long edge is <= max, preserving aspect ratio; never upscale. */
export function targetDimensions(
  width: number,
  height: number,
  max = MAX_LONG_EDGE,
): { width: number; height: number } {
  const longEdge = Math.max(width, height)
  if (longEdge <= max) return { width, height }
  const scale = max / longEdge
  return { width: Math.round(width * scale), height: Math.round(height * scale) }
}

interface Decoded {
  source: CanvasImageSource
  width: number
  height: number
  close: () => void
}

async function decodeViaImageElement(file: File): Promise<Decoded> {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('image decode failed'))
      el.src = url
    })
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, close: () => {} }
  } finally {
    // Pixels are decoded once onload fires; the URL is safe to revoke here.
    URL.revokeObjectURL(url)
  }
}

async function decode(file: File): Promise<Decoded> {
  try {
    // Pin EXIF handling explicitly so camera photos keep their rotation.
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
    return { source: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() }
  } catch {
    // Some browsers reject formats in createImageBitmap that <img> can still decode.
    return decodeViaImageElement(file)
  }
}

/**
 * Re-encode a photo as a downscaled JPEG for upload. On any failure
 * (undecodable format, no canvas context, encoder refusal) the original file
 * is returned unchanged — the server remains the backstop — never throws.
 */
export async function prepareImage(file: File): Promise<File> {
  if (file.type === 'image/jpeg' && file.size <= SKIP_RE_ENCODE_BYTES) return file

  try {
    const decoded = await decode(file)
    try {
      const { width, height } = targetDimensions(decoded.width, decoded.height)
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const ctx = canvas.getContext('2d')
      if (!ctx) return file
      ctx.drawImage(decoded.source, 0, 0, width, height)
      const blob = await new Promise<Blob | null>(resolve =>
        canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY),
      )
      if (!blob) return file
      const name = file.name.replace(/\.[^./]*$/, '') + '.jpg'
      return new File([blob], name, { type: 'image/jpeg' })
    } finally {
      decoded.close()
    }
  } catch {
    // e.g. true HEIC with no OS decoder: upload as-is and let the server cope.
    return file
  }
}
