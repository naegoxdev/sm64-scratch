"""N64 byte-order normalization, MIO0 and image decoding for local ROM builds."""
import struct, hashlib
from PIL import Image
ROM_SHA1="9bef1128717f958171a4afac3ed78ee2bb4e86ce"

def normalized_rom(data):
    h=data[:4]
    if h==bytes.fromhex('80371240'):return data
    if h==bytes.fromhex('37804012'):step=2
    elif h==bytes.fromhex('40123780'):step=4
    else:raise ValueError('Unrecognized N64 ROM byte order')
    if len(data)%step:raise ValueError('Invalid ROM length')
    b=bytearray(data)
    for k in range(0,len(b),step):b[k:k+step]=b[k:k+step][::-1]
    return bytes(b)


def mio0(blob,pos,max_out=32*1024*1024):
    """Nintendo MIO0: flag=1 copy a literal; flag=0 copy LZ run."""
    if blob[pos:pos+4]!=b'MIO0' or pos+16>len(blob):raise ValueError('Invalid MIO0 header')
    size, comp_off, literal_off=struct.unpack_from('>III',blob,pos+4)
    if not (0<size<=max_out and 16<=comp_off<=literal_off<len(blob)-pos):raise ValueError('MIO0 header out of bounds')
    ctrl=pos+16; c=pos+comp_off; lit=pos+literal_off; out=bytearray(); flags=0;bits=0
    while len(out)<size:
        if bits==0:
            if ctrl+4>len(blob):raise ValueError('MIO0 flags out of bounds')
            flags=struct.unpack_from('>I',blob,ctrl)[0];ctrl+=4;bits=32
        take_lit=bool(flags&0x80000000);flags=(flags<<1)&0xffffffff;bits-=1
        if take_lit:
            if lit>=len(blob):raise ValueError('MIO0 literal out of bounds')
            out.append(blob[lit]);lit+=1
        else:
            if c+2>len(blob):raise ValueError('MIO0 reference out of bounds')
            token=struct.unpack_from('>H',blob,c)[0];c+=2
            count=(token>>12)+3;distance=(token&0xfff)+1
            if distance>len(out) or len(out)+count>size:raise ValueError('Invalid MIO0 reference')
            for _ in range(count):out.append(out[-distance])
    return bytes(out)


def decode_rgba16(region,offset,width,height,mode):
    pixels=bytearray(width*height*4)
    for i in range(width*height):
        v=(region[offset+2*i]<<8)|region[offset+2*i+1]
        c=[(v>>11)&31,(v>>6)&31,(v>>1)&31]
        if mode=='bitrep':rgb=[(n<<3)|(n>>2) for n in c]
        elif mode=='round':rgb=[round(n*255/31) for n in c]
        elif mode=='floor':rgb=[n*255//31 for n in c]
        else:raise ValueError('Unknown RGB expansion')
        pixels[4*i:4*i+4]=bytes(rgb+[255 if v&1 else 0])
    return Image.frombytes('RGBA',(width,height),bytes(pixels))


def source_sha(im):return hashlib.sha256(im.convert('RGBA').tobytes()).hexdigest()



def decode_ia16(region,offset,width,height):
    pixels=bytearray(width*height*4)
    for i in range(width*height):
        intensity=region[offset+2*i];alpha=region[offset+2*i+1]
        pixels[4*i:4*i+4]=bytes((intensity,intensity,intensity,alpha))
    return Image.frombytes('RGBA',(width,height),bytes(pixels))


def decode_ia8(region,offset,width,height):
    pixels=bytearray(width*height*4)
    for i in range(width*height):
        value=region[offset+i];intensity=((value>>4)&15)*17;alpha=(value&15)*17
        pixels[4*i:4*i+4]=bytes((intensity,intensity,intensity,alpha))
    return Image.frombytes('RGBA',(width,height),bytes(pixels))
