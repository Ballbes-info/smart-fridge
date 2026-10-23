"""Label field extraction from detected text regions, independent of the OCR engine.

Numbers are copied, never repaired by guessing a missing decimal point. Coordinates
keep unrelated columns apart and preserve the first (100 g/ml) nutrition column.
"""
import math
import re
from datetime import date
from statistics import median

NUTRITION_WARNINGS = ('КБЖУ не согласуется с калорийностью. Проверьте цифры и выбранную колонку таблицы.',
                      'Не определено, к какому количеству относятся КБЖУ.')

NUMBER = r'\d{1,6}(?:[.,]\d{1,3})?'
FOOD = re.compile(r'молок[оа]|кефир|творог|сметан|йогурт|сыр\b|масло|котлет|пельмен|фарш|мясо|свинин|говядин|куриц|шоколад|батончик|вафл|печень|конфет|крупа|греч|рис\b|макарон|хлеб|сок\b|вода\b|банан|яблок|рыб[аы]|колбас|сосиск',re.I)
TECH = re.compile(r'состав|марка\s*[:—-]|бренд\s*[:—-]|пищев|ценност|энергет|калор|белк|углевод|ккал|кдж|изготов|производител|хранит|годен|срок|масса|объ[её]м|^\W*(?:гост|стб|ту\b|еас|ean)|www\.|@|\d{5,}|росси[яи]|адрес|упаковк|после\s+вскры',re.I)


# OCR often inserts punctuation inside headings (e.g. 'Пищевая. ценность').
INGREDIENT_STOP = re.compile(r'(?:\b(?:в\s*)?пищев[а-яё]*[\W_]{0,6}ценност[а-яё]*|'
                             r'\bэнергетическ[а-яё]*[\W_]{0,6}ценност[а-яё]*|'
                             r'\bусловия\s+хранения|\bхранить\s+при|\bсрок\s+годности|'
                             r'\bгоден\s+до|\bизготовитель)',re.I)


def ingredients_only(text):
    stop=INGREDIENT_STOP.search(text)
    return re.sub(r'\s+',' ',text[:stop.start()] if stop else text).strip(' \n|;')[:3000]


def normalize(text):
    text = text.replace('\u00a0',' ').replace('−','-')
    # Latin look-alikes inside Cyrillic words are OCR artefacts; English brand names stay unchanged.
    table=str.maketrans('ABCEHKMOPTXacekopxy','АВСЕНКМОРТХасекорху')
    text=re.sub(r'[A-Za-zА-Яа-яЁё]+',lambda m:m.group().translate(table) if re.search('[А-Яа-яЁё]',m.group()) else m.group(),text)
    text = re.sub(r'(?<=\d)\s*([,.])\s*(?=\d)',r'\1',text)
    return text


def text_regions(regions):
    """Validate the geometry returned by the OCR worker."""
    rows=[]
    for region in regions[:2000]:
        if not isinstance(region,dict): continue
        text=normalize(str(region.get('text',''))).strip()
        try:
            x,y,w,h=[float(region[key]) for key in ['x','y','w','h']]
            confidence=float(region.get('confidence',0))
        except (ValueError,TypeError,KeyError):continue
        if not text or not all(math.isfinite(v) for v in [x,y,w,h,confidence]) or w<=0 or h<=0:continue
        rows.append(dict(text=text,x=x,y=y,w=w,h=h,confidence=max(0,min(1,confidence))))
    rows.sort(key=lambda r:(r['y']+r['h']/2,r['x']))
    return rows


def lines_from_regions(regions):
    """Join neighbouring pieces of a line without crossing a wide column gap."""
    rows=text_regions(regions)
    bands=[]
    for row in rows:
        cy=row['y']+row['h']/2
        band=next((b for b in reversed(bands[-8:]) if abs(b['cy']-cy)<=.4*min(b['h'],row['h'])),None)
        if band is None:bands.append(dict(cy=cy,h=row['h'],parts=[row]))
        else:band['parts'].append(row)
    result=[]
    for band in bands:
        group=[]
        for row in sorted(band['parts'],key=lambda r:r['x']):
            if group and row['x']-(group[-1]['x']+group[-1]['w'])>max(45,2.5*min(row['h'],group[-1]['h'])):
                result.append(combine(group));group=[]
            group.append(row)
        if group:result.append(combine(group))
    return sorted(result,key=lambda r:(r['y'],r['x']))


def combine(group):
    x=min(r['x'] for r in group);y=min(r['y'] for r in group)
    return dict(text=' '.join(r['text'] for r in group),x=x,y=y,
                w=max(r['x']+r['w'] for r in group)-x,h=max(r['h'] for r in group),
                confidence=min(r['confidence'] for r in group))


def extract(regions):
    lines=lines_from_regions(regions)
    original=text_regions(regions)
    headers=[r for r in original+lines if re.fullmatch(r'\s*(?:на\s*)?100\s*(?:г|g|мл|ml)(?:\s+продукта)?\s*',r['text'],re.I)]
    # Each left edge defines a reading column. Only use this ordering for prose;
    # nutrition matching below separately uses geometric neighbours.
    parsed=parse_label('\n'.join(r['text'] for r in lines))
    proposals=parsed['proposals'];confidence=parsed['confidence'];warnings=parsed['warnings'];warnings.clear()
    for key,label in [('protein',r'бел(?:о)?к\w*'),('fat',r'жир[ыа]?\b'),('carbs',r'углевод\w*')]:
        candidates=[]
        for row in lines:
            match=re.search(label,row['text'],re.I)
            if not match:continue
            tail=row['text'][match.end():]
            own=re.search(NUMBER,tail)
            anchors=[h for h in headers if 0<row['y']-h['y']<row['h']*15]
            anchor=min(anchors,key=lambda h:row['y']-h['y']) if anchors else None
            right=[r for r in original if r['x']>row['x']+row['h'] and
                   abs((r['y']+r['h']/2)-(row['y']+row['h']/2))<.65*max(row['h'],r['h']) and
                   re.fullmatch(r'\s*'+NUMBER+r'\s*(?:г|g)?\s*',r['text'],re.I)]
            if right:
                neighbour=min(right,key=lambda r:abs((r['x']+r['w']/2)-(anchor['x']+anchor['w']/2))) if anchor else min(right,key=lambda r:r['x'])
                value=re.search(NUMBER,neighbour['text']).group();score=min(row['confidence'],neighbour['confidence'])
            elif own:
                value=own.group();score=row['confidence']
                if len(re.findall(NUMBER,tail))>1 and anchor is None:score=min(score,.5)
            else:continue
            if float(value.replace(',','.'))<=100:candidates.append((value.replace(',','.'),score))
        unique={v for v,c in candidates}
        if len(unique)==1:
            proposals[key]=candidates[0][0];confidence[key]=min(c for v,c in candidates)
        elif len(unique)>1:
            proposals.pop(key,None);confidence.pop(key,None);warnings.append('Разные значения БЖУ на фото. Проверьте таблицу на упаковке.')
    ingredient_lines=set()
    # Recover ingredients in their own column rather than interleaved manufacturer copy.
    for row in lines:
        match=re.search(r'состав\s*[:;—-]?\s*(.*)',row['text'],re.I)
        if not match:continue
        inline=ingredients_only(match.group(1))
        chunks=[inline] if inline else []
        heading_in_row=bool(INGREDIENT_STOP.search(match.group(1)))
        conf=[row['confidence']]
        bottom=row['y']+row['h']
        for following in sorted(lines,key=lambda r:r['y']):
            if heading_in_row:break
            if following['y']<bottom-row['h']*.2:continue
            if abs(following['x']-row['x'])>max(35,row['w']*.25):continue
            if following['y']-bottom>row['h']*2.8:break
            if INGREDIENT_STOP.search(following['text']) or re.search(r'пищев|ценност|энергет|калорий|срок|годен|хранить|изготовитель',following['text'],re.I):break
            ingredient_lines.add(following['text']);chunks.append(following['text']);conf.append(following['confidence']);bottom=following['y']+following['h']
        if chunks:proposals['ingredients']=' '.join(chunks)[:3000];confidence['ingredients']=min(conf)
        break
    # Name should be the visible product heading, not a standard number or a random logo.
    typical=median([r['h'] for r in lines]) if lines else 1
    names=[r for r in lines if valid_name(r['text']) and r['text'] not in ingredient_lines]
    if names:
        names.sort(key=lambda r:(bool(FOOD.search(r['text'])),r['h']/typical,r['confidence']),reverse=True)
        best=names[0]
        proposals['name']=best['text'][:120];confidence['name']=best['confidence'] if FOOD.search(best['text']) else min(.65,best['confidence'])
        # Adjacent equally large lines are a multi-line product title.
        adjacent=[r for r in lines if r is not best and valid_name(r['text']) and r['text'] not in ingredient_lines and
                  r['y']>=best['y']+best['h']*.5 and r['y']<=best['y']+best['h']*2.8 and
                  r['h']>=best['h']*.65 and abs(r['x']-best['x'])<max(best['w'],r['w'])*.6]
        if adjacent:
            title=best['text']+' '+min(adjacent,key=lambda r:r['y'])['text']
            if len(title)<=120:proposals['name']=title
    # Separate printed date rows belong to the nearest unambiguous date heading.
    parsed.setdefault('alternatives',{})
    date_rows=[]
    for row in original:
        if re.fullmatch(r'\s*\d{2}[./-]\d{2}[./-](?:\d{4}|\d{2})\s*',row['text']):
            date_rows.append(row)
    # Flat reading order cannot bind dates under two side-by-side headings.
    separate_dates={value for row in date_rows for value in parse_label(row['text'])['dates']}
    if separate_dates:
        for key in ['manufactured_date','expiry_date']:
            if proposals.get(key) in separate_dates:proposals.pop(key,None);confidence.pop(key,None)
    for row in date_rows:
        dm=re.fullmatch(r'\s*(\d{2})[./-](\d{2})[./-](\d{4}|\d{2})\s*',row['text'])
        try:
            day,month,year=map(int,dm.groups());year=year+2000 if year<100 else year
            if not 1900<=year<=2200:continue
            value=date(year,month,day).isoformat()
        except ValueError:continue
        choices=[]
        for header in original+lines:
            manufactured=bool(re.search(r'дата\s*(?:изготовления|производства)|изготовлен[оа]?',header['text'],re.I))
            expires=bool(re.search(r'годен\s*до|употребить\s*до',header['text'],re.I))
            if manufactured==expires:continue
            distance=row['y']-(header['y']+header['h'])
            center=row['x']+row['w']/2
            vertical=(-.3*header['h']<=distance<=3*max(header['h'],row['h']) and header['x']-row['h']<=center<=header['x']+header['w']+row['h'])
            horizontal=(abs((row['y']+row['h']/2)-(header['y']+header['h']/2))<.6*max(header['h'],row['h']) and 0<=row['x']-header['x']-header['w']<=6*row['h'])
            if vertical or horizontal:
                choices.append((abs(distance) if vertical else row['x']-header['x']-header['w'],'manufactured_date' if manufactured else 'expiry_date',header['confidence']))
        if choices:
            choices.sort();_,key,score=choices[0]
            if key in proposals and proposals[key]!=value:
                parsed['alternatives'][key]=list(dict.fromkeys([proposals[key],value]));confidence[key]=0
            else:proposals[key]=value;confidence[key]=min(score,row['confidence'])
    # Read a stacked kJ/kcal row in the same 100g/ml column, not a portion column.
    if 'calories' not in proposals:
        for row in original:
            if not re.search(r'ккал',row['text'],re.I):continue
            anchors=[h for h in headers if 0<row['y']-h['y']<row['h']*20]
            if not anchors:continue
            anchor=min(anchors,key=lambda h:row['y']-h['y'])
            next_rows=[r['y'] for r in original if r['y']>row['y']+.5*row['h'] and re.search(r'бел(?:о)?к|жир|углевод',r['text'],re.I)]
            stop=min(next_rows) if next_rows else row['y']+2.5*row['h']+.1
            numeric=[r for r in original if row['y']-.4*row['h']<=r['y']<=row['y']+2.5*row['h'] and r['y']<stop and
                     re.fullmatch(NUMBER,r['text']) and float(r['text'].replace(',','.'))<=1000 and
                     abs(r['x']+r['w']/2-anchor['x']-anchor['w']/2)<max(anchor['w'],r['w'])]
            if numeric:
                chosen=max(numeric,key=lambda r:r['y'])
                proposals['calories']=chosen['text'].replace(',','.');confidence['calories']=min(chosen['confidence'],row['confidence'])
                break
    # A weight in a nutrition column heading is a portion, not the packet's net weight.
    if 'quantity' in proposals:
        for row in original:
            weight=re.fullmatch(r'\s*('+NUMBER+r')\s*(г|g|мл|ml)\s*',row['text'],re.I)
            if not weight or weight.group(1).replace(',','.')!=proposals['quantity']:continue
            if any(abs(row['y']-h['y'])<max(row['h'],h['h']) for h in headers):
                for key in ['quantity','unit']:proposals.pop(key,None);confidence.pop(key,None)
                break
    # Raise confidence only from visible supporting lines; no fabricated percentages.
    for key in list(proposals):
        if key in ['name','ingredients','protein','fat','carbs']:continue
        value=proposals[key]
        supporting=[r['confidence'] for r in lines if value.casefold() in r['text'].replace(',','.').casefold()]
        if supporting:confidence[key]=min(supporting)
    parsed['reading_confidence']=dict(confidence)
    validate_nutrition(parsed)
    parsed['warnings']=list(dict.fromkeys(parsed['warnings']))
    return parsed


def valid_name(line):
    letters=len(re.findall(r'[а-яa-z]',line,re.I))
    return (3<=len(line)<=120 and letters>=4 and letters/max(1,len(line))>.45 and
            not TECH.search(line) and not re.match(r'^\W*\d',line))


def parse_label(text):
    text=normalize(text[:30000]);p={};confidence={};warnings=[];dates=[]
    def put(key,value,score=.8):p[key]=value;confidence[key]=score
    for field,pattern in [('protein',r'бел(?:о)?к\w*'),('fat',r'жир[ыа]?\b'),('carbs',r'углевод\w*')]:
        match=re.search(pattern+r'\s*(?:(?:[:—–-]|не\s+(?:более|менее)|[≤≥<>])\s*)*('+NUMBER+r')\s*(?:г\b|g\b)?',text,re.I)
        if match and float(match.group(1).replace(',','.'))<=100:
            put(field,match.group(1).replace(',','.'))
            if re.search(r'не\s+(?:более|менее)|[≤≥<>]',match.group(),re.I):
                put('other_info',(p.get('other_info','')+'\n'+match.group().strip()).strip())
    match=re.search('('+NUMBER+r')\s*ккал',text,re.I)
    if not match:match=re.search(r'(?:кдж\s*[/|]\s*ккал)\s*[:—–-]?\s*'+NUMBER+r'\s*[/|]\s*('+NUMBER+r')',text,re.I)
    if match:put('calories',match.group(1).replace(',','.'))
    if re.search(r'(?:на\s*|ценност\w*\s*(?:в\s*)?|в\s*)100\s*(?:мл|ml)\b',text,re.I):put('nutrition_basis','100ml')
    elif re.search(r'(?:на\s*|ценност\w*\s*(?:в\s*)?|в\s*)100\s*(?:г|g)\b',text,re.I):put('nutrition_basis','100g')
    for field,label in [('brand',r'(?:марка|бренд)'),('manufacturer',r'(?:изготовитель|производитель)')]:
        match=re.search(label+r'\s*[:—-]\s*([^\n]{2,160})',text,re.I)
        if match:put(field,match.group(1).strip())
    match=re.search(r'состав\s*[:;—-]?\s*(.+?)(?=\s*(?:пищевая\s+ценность|энергетическая\s+ценность|условия\s+хранения|хранить\s+при|срок\s+годности|годен\s+до|изготовитель)|$)',text,re.I|re.S)
    if match:
        composition=ingredients_only(match.group(1))
        if composition:put('ingredients',composition)
    # Product amount only, not a portion heading or values in a nutrition table.
    match=re.search(r'(?:масса\s*(?:нетто)?|объ[её]м|содержимое)\s*[:—-]?\s*((?:\d\s*){1,6}(?:[.,]\d+)?)\s*(кг|мл|г|л)\b',text,re.I)
    reversed_amount=None
    if not match:
        reversed_amount=re.search(r'(?:масса\s*(?:нетто)?|объ[её]м|содержимое)\s*[:—-]?\s*(кг|мл|г|л)\s*[:—-]?\s*((?:\d[ \t]*){1,6}(?:[.,]\d+)?)(?![\d])',text,re.I)
        if reversed_amount:
            amount=re.sub(r'\s','',reversed_amount.group(2)).replace(',','.')
            if 0<float(amount)<=1000000:
                put('quantity',amount);put('unit',{'кг':'kg','г':'g','мл':'ml','л':'l'}[reversed_amount.group(1).lower()])
    if not match:
        match=next((m for m in re.finditer(r'(?<![\d.,])('+NUMBER+r')\s*(кг|мл|г|л)\b',text,re.I)
                    if not re.search(r'100\s*$|на\s*$|в\s*$|порци|белк|жир|углевод',text[max(0,m.start()-35):m.start()].split('\n')[-1],re.I)
                    and not re.search(r'(?:на|в)\s*100\s*(?:г|мл)',text[:m.start()].split('\n')[-1],re.I)
                    and m.group(1)!='100' and not re.search(r'ккал|кдж',text[m.end():m.end()+8],re.I)
                    and not any(word in text[max(0,m.start()-80):m.start()].casefold() for word in ['пищевая','ценность','калорий'])),None)
    if match:
        amount=re.sub(r'\s','',match.group(1)).replace(',','.')
        if float(amount)>0:
            put('quantity',amount);put('unit',{'кг':'kg','г':'g','мл':'ml','л':'l'}[match.group(2).lower()])
    for m in re.finditer(r'(?<!\d)(\d{2})[./-](\d{2})[./-](\d{4}|\d{2})(?!\d)',text):
        try:
            d,month,y=map(int,m.groups());y=y+2000 if y<100 else y
            if not 1900<=y<=2200:continue
            value=date(y,month,d).isoformat();dates.append(value)
            prefix=text[max(0,m.start()-65):m.start()]
            # A heading saying both dates are elsewhere is not a labelled printed date.
            if re.search(r'годен\s*до\s*[:—-]?\s*$',prefix,re.I):put('expiry_date',value)
            elif re.search(r'(?:изготовлен[оа]?|дата\s*(?:изготовления|производства))\s*[:—-]?\s*$',prefix,re.I):put('manufactured_date',value)
        except ValueError:pass
    match=re.search(r'срок\s*годности[^\d]{0,120}\s*(\d{1,4})\s*(сут\w*|дн\w*|месяц\w*|мес\.?|лет|год\w*)',text,re.I)
    if match:
        put('shelf_value',match.group(1));put('shelf_unit','months' if match.group(2).lower().startswith('мес') else 'years' if match.group(2).lower().startswith(('лет','год')) else 'days')
    names=[line.strip() for line in text.splitlines() if valid_name(line.strip())]
    if names:
        best=next((line for line in names if FOOD.search(line)),names[0]);put('name',best,.85 if FOOD.search(best) else .6)
    extra=[p.get('other_info','')]
    if 'shelf_value' in p:extra.append('Срок годности: '+p['shelf_value']+' '+{'days':'дней','months':'месяцев','years':'лет'}[p['shelf_unit']])
    for m in re.finditer(r'(?:хранить\s+при|условия\s+хранения|после\s+вскрытия|при\s+температуре\s+хранения)[^\n]{0,240}',text,re.I):extra.append(m.group().strip())
    if any(extra):put('other_info','\n'.join(dict.fromkeys(v for v in extra if v)))
    result={'proposals':p,'confidence':confidence,'dates':list(dict.fromkeys(dates)),'text':text,'warnings':warnings}
    result['reading_confidence']=dict(confidence)
    validate_nutrition(result)
    return result


def validate_nutrition(result):
    p=result['proposals'];confidence=result['confidence'];warnings=result['warnings']
    if p.get('nutrition_basis') in ['100g','100ml'] and all(k in p for k in ['calories','protein','fat','carbs']):
        calculated=4*float(p['protein'])+9*float(p['fat'])+4*float(p['carbs']);printed=float(p['calories'])
        if abs(calculated-printed)>max(30,printed*.3):
            for key in ['calories','protein','fat','carbs']:confidence[key]=min(confidence.get(key,.5),.4)
            warnings.append('КБЖУ не согласуется с калорийностью. Проверьте цифры и выбранную колонку таблицы.')
    if not p.get('nutrition_basis') and any(k in p for k in ['calories','protein','fat','carbs']):
        for key in ['calories','protein','fat','carbs']:
            if key in confidence:confidence[key]=min(confidence[key],.5)
        warnings.append('Не определено, к какому количеству относятся КБЖУ.')
