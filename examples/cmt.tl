#pragma version 7

# Setup vars
int x = 2 # 2 is the only even prime
int y = 3

# Add vars
int z = x + y

assert(z>0, "z is not greater than 0")

log(itob(z))

exit(1)