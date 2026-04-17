import struct
import os

def inspect_png(filepath):
    if not os.path.exists(filepath):
        print(f"{filepath}: File not found")
        return
    
    chunks = []
    exif_count = 0
    with open(filepath, 'rb') as f:
        sig = f.read(8)
        if sig != b'\x89PNG\r\n\x1a\n':
            print(f"{filepath}: Not a valid PNG")
            return
        
        while True:
            length_data = f.read(4)
            if not length_data:
                break
            length = struct.unpack('>I', length_data)[0]
            chunk_type = f.read(4).decode('ascii', errors='ignore')
            chunks.append(chunk_type)
            if chunk_type == 'eXIf':
                exif_count += 1
            f.seek(length + 4, 1)  # Skip data and CRC
            if chunk_type == 'IEND':
                break
    
    print(f"File: {filepath}")
    print(f"Chunks: {', '.join(chunks)}")
    print(f"Multiple eXIf: {'Yes' if exif_count > 1 else 'No'} ({exif_count})")
    if exif_count > 1:
        print(f"ALERT: {filepath} has duplicate eXIf chunks")
    print("-" * 20)

files = [
    'assets/images/favicon.png',
    'assets/images/OpenwaterLogo.png',
    'assets/images/empty_graph.png'
]

for f in files:
    inspect_png(f)
