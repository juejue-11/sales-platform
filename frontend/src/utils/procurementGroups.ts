import type { ProcurementItem } from '../types'

/**
 * A display group for procurement rows that share the same named material.
 * `items` keeps the original source objects and their source order so callers
 * can expand a group without losing provenance or per-spec quantities.
 */
export interface ProcurementGroup {
  key: string
  name: string
  material: string
  items: ProcurementItem[]
  specifications: string[]
  specificationSummary: string
  quantitySummary: string
}

interface DecimalValue {
  digits: bigint
  scale: number
}

interface MutableGroup {
  key: string
  name: string
  material: string
  items: ProcurementItem[]
}

const unknownUnitValues = new Set(['?', '？', '-', '–', '—', '未知', '不详', '未注明', '待定', 'n/a', 'na', 'none', 'null'])

/** Normalize presentation whitespace without changing any other source text. */
function normalizeWhitespace(value: unknown): string {
  if (value === null || value === undefined) return ''
  return String(value).replace(/\s+/g, ' ').trim()
}

function parseNonNegativeDecimal(value: unknown): DecimalValue | null {
  const text = normalizeWhitespace(value)
  // Deliberately require the complete value to be numeric. Ranges, prose,
  // negatives, and other uncertain quantities must not enter a total.
  if (!/^\+?(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)) return null

  const unsigned = text.startsWith('+') ? text.slice(1) : text
  const [integerPart, fractionPart = ''] = unsigned.split('.')
  const allDigits = `${integerPart || '0'}${fractionPart}`.replace(/^0+(?=\d)/, '') || '0'
  return { digits: BigInt(allDigits), scale: fractionPart.length }
}

function powerOfTen(exponent: number): bigint {
  return 10n ** BigInt(exponent)
}

function compareDecimal(left: DecimalValue, right: DecimalValue): number {
  const scale = Math.max(left.scale, right.scale)
  const leftDigits = left.digits * powerOfTen(scale - left.scale)
  const rightDigits = right.digits * powerOfTen(scale - right.scale)
  return leftDigits < rightDigits ? -1 : leftDigits > rightDigits ? 1 : 0
}

function addDecimal(left: DecimalValue, right: DecimalValue): DecimalValue {
  const scale = Math.max(left.scale, right.scale)
  return {
    digits: left.digits * powerOfTen(scale - left.scale) + right.digits * powerOfTen(scale - right.scale),
    scale,
  }
}

function decimalText(value: DecimalValue): string {
  if (value.scale === 0) return value.digits.toString()

  const raw = value.digits.toString().padStart(value.scale + 1, '0')
  const integerPart = raw.slice(0, -value.scale) || '0'
  const fractionPart = raw.slice(-value.scale).replace(/0+$/, '')
  return fractionPart ? `${integerPart}.${fractionPart}` : integerPart
}

interface SimpleNumericSpecification {
  value: DecimalValue
  suffix: string
}

function parseSimpleNumericSpecification(specification: string): SimpleNumericSpecification | null {
  const match = specification.match(/^(\+?(?:\d+(?:\.\d+)?|\.\d+))\s*(\S.*)$/)
  if (!match) return null

  const suffix = match[2].trim()
  // A suffix containing digits or dimensional/range punctuation indicates a
  // shape such as 10x20mm or 4-20mm, rather than one numeric value plus unit.
  if (!suffix || /\d/.test(suffix) || /[-–—~～至\/×x＊*]/u.test(suffix)) return null

  const value = parseNonNegativeDecimal(match[1])
  return value ? { value, suffix } : null
}

function specificationSummary(specifications: string[]): string {
  if (specifications.length === 0) return '规格待核对'

  if (specifications.length >= 4) {
    const parsed = specifications.map(parseSimpleNumericSpecification)
    const suffix = parsed[0]?.suffix
    if (suffix && parsed.every((entry) => entry?.suffix === suffix)) {
      const values = parsed.map((entry) => entry as SimpleNumericSpecification)
      let minimum = values[0].value
      let maximum = values[0].value
      for (const entry of values.slice(1)) {
        if (compareDecimal(entry.value, minimum) < 0) minimum = entry.value
        if (compareDecimal(entry.value, maximum) > 0) maximum = entry.value
      }
      return `${decimalText(minimum)}–${decimalText(maximum)}${suffix}`
    }
  }

  const preview = specifications.slice(0, 3).join('、')
  return specifications.length > 3 ? `${preview}等${specifications.length}种规格` : preview
}

function quantitySummary(items: ProcurementItem[]): string {
  const totals = new Map<string, DecimalValue>()
  let incompleteCount = 0

  for (const item of items) {
    const quantity = parseNonNegativeDecimal(item?.quantity)
    const unit = normalizeWhitespace(item?.unit)
    if (!quantity || !unit || unknownUnitValues.has(unit.toLowerCase())) {
      incompleteCount += 1
      continue
    }

    const previous = totals.get(unit)
    totals.set(unit, previous ? addDecimal(previous, quantity) : quantity)
  }

  const totalText = Array.from(totals.entries())
    .map(([unit, total]) => `${decimalText(total)} ${unit}`)
    .join('；')

  if (incompleteCount === 0) return totalText || '数量待核对'
  const incompleteText = `另有${incompleteCount}项数量待核对`
  return totalText ? `${totalText}；${incompleteText}` : incompleteText
}

function groupKey(name: string, material: string, sourceIndex: number): string {
  // JSON encoding avoids collisions when a source name or material contains a
  // delimiter, while staying stable when a complete group is filtered or
  // reordered. Incomplete rows remain intentionally separate.
  return name && material ? JSON.stringify([name, material]) : `missing:${sourceIndex}`
}

/**
 * Group procurement rows by normalized name and material.
 *
 * This function is pure: it does not mutate the input array or any source
 * item. Complete rows group regardless of unit; totals are calculated later
 * per exact normalized unit, with uncertain quantities kept as an explicit
 * incomplete count.
 */
export function groupProcurementItems(items: ProcurementItem[]): ProcurementGroup[] {
  const groups: MutableGroup[] = []
  const byKey = new Map<string, MutableGroup>()

  items.forEach((item, sourceIndex) => {
    const name = normalizeWhitespace(item?.name)
    const material = normalizeWhitespace(item?.material)
    const key = groupKey(name, material, sourceIndex)
    let group = byKey.get(key)
    if (!group) {
      group = { key, name, material, items: [] }
      byKey.set(key, group)
      groups.push(group)
    }
    group.items.push(item)
  })

  return groups.map((group) => {
    const specifications: string[] = []
    const seenSpecifications = new Set<string>()
    for (const item of group.items) {
      const specification = normalizeWhitespace(item?.specification)
      if (specification && !seenSpecifications.has(specification)) {
        seenSpecifications.add(specification)
        specifications.push(specification)
      }
    }

    return {
      ...group,
      specifications,
      specificationSummary: specificationSummary(specifications),
      quantitySummary: quantitySummary(group.items),
    }
  })
}
