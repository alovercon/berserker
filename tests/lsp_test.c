#include <stdio.h>
#include <stdlib.h>

typedef struct {
    int x;
    int y;
} Point;

void print_point(Point *p) {
    printf("Point(%d, %d)\n", p->x, p->y);
}

Point create_point(int x, int y) {
    Point p;
    p.x = x;
    p.y = y;
    return p;
}

int main() {
    Point origin = create_point(0, 0);
    print_point(&origin);
    return 0;
}
