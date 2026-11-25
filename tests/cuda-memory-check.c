#include <dlfcn.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

#define CUDA_CALL(call) do { \
    status = (call); \
    if (status) { fprintf(stderr, "%s: CUDA error %d\n", #call, status); return 1; } \
} while (0)

int main(void)
{
    void *cuda = dlopen("libcuda.so.1", RTLD_NOW);
    if (!cuda) {
        fprintf(stderr, "CUDA runtime: %s\n", dlerror());
        return 1;
    }
    int (*init)(unsigned int) = dlsym(cuda, "cuInit");
    int (*device_get)(int *, int) = dlsym(cuda, "cuDeviceGet");
    int (*create)(void **, unsigned int, int) = dlsym(cuda, "cuCtxCreate_v2");
    int (*allocate)(uint64_t *, size_t) = dlsym(cuda, "cuMemAlloc_v2");
    int (*fill)(uint64_t, unsigned int, size_t) = dlsym(cuda, "cuMemsetD32_v2");
    int (*copy)(void *, uint64_t, size_t) = dlsym(cuda, "cuMemcpyDtoH_v2");
    int (*release)(uint64_t) = dlsym(cuda, "cuMemFree_v2");
    int (*destroy)(void *) = dlsym(cuda, "cuCtxDestroy_v2");
    if (!init || !device_get || !create || !allocate || !fill || !copy || !release || !destroy) {
        fputs("Required CUDA driver entry point unavailable\n", stderr);
        return 1;
    }

    const uint32_t expected = 0x47a1057u;
    uint32_t actual = 0;
    uint64_t gpu_memory = 0;
    void *context = NULL;
    int device = 0, status = 0;

    CUDA_CALL(init(0));
    CUDA_CALL(device_get(&device, 0));
    CUDA_CALL(create(&context, 0, device));
    CUDA_CALL(allocate(&gpu_memory, sizeof(actual)));
    CUDA_CALL(fill(gpu_memory, expected, 1));
    CUDA_CALL(copy(&actual, gpu_memory, sizeof(actual)));
    CUDA_CALL(release(gpu_memory));
    CUDA_CALL(destroy(context));
    dlclose(cuda);

    printf("CUDA device %d memory write/read: 0x%08" PRIx32 "\n", device, actual);
    return actual == expected ? 0 : 1;
}
