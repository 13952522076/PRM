import torch.nn as nn
from torch.nn.parameter import Parameter
import torch
import time
import numpy as np
from torch.distributions.normal import Normal


__all__ = ['prm_resnet18','prm_resnet34','prm_resnet50','prm_resnet101','prm_resnet152']

"""
group is the number of selected points.
"""


class PRMLayer(nn.Module):
    def __init__(self,groups=64,mode='dotproduct'):
        super(PRMLayer, self).__init__()
        self.mode = mode
        self.groups = groups
        self.max_pool = nn.AdaptiveMaxPool2d(1,return_indices=True)
        self.weight = Parameter(torch.zeros(1,self.groups,1,1))
        self.bias = Parameter(torch.ones(1,self.groups,1,1))
        self.sig = nn.Sigmoid()
        self.gap = nn.AdaptiveAvgPool2d(1)
        self.one = Parameter(torch.ones(1,self.groups,1))
        self.zero = Parameter(torch.zeros(1, self.groups, 1))
        self.theta = Parameter(torch.rand(1,2,1,1))
        self.scale =  Parameter(torch.ones(1))

    def forward(self, x):

        b,c,h,w = x.size()
        position_mask = self.get_position_mask(x, b, h, w, self.groups)
        # Similarity function
        query_value, query_position = self.get_query_position(x, self.groups)  # shape [b*num,2,1,1]
        # print(query_position.float()/h)
        query_value = query_value.view(b*self.groups,-1,1)
        x_value = x.view(b*self.groups,-1,h*w)
        similarity_max = self.get_similarity(x_value, query_value, mode=self.mode)
        similarity_gap = self.get_similarity(x_value, self.gap(x).view(b*self.groups,-1,1), mode=self.mode)

        similarity_max = similarity_max.view(b,self.groups,h*w)

        Distance = abs(position_mask - query_position)
        Distance = Distance.type(query_value.type())
        # Distance = torch.exp(-Distance * self.theta)
        distribution = Normal(0, self.scale)
        Distance = distribution.log_prob(Distance * self.theta).exp().clone()
        Distance = (Distance.mean(dim=1)).view(b, self.groups, h * w)
        print_Dis = Distance.mean(dim=0).mean(dim=0).view(h,w)
        np.savetxt(time.perf_counter().__str__()+'.txt',print_Dis.detach().cpu().numpy())
        # # add e^(-x), means closer more important
        # Distance = torch.exp(-Distance * self.theta)
        # Distance = (self.distance_embedding(Distance)).reshape(b, self.groups, h*w)
        similarity_max = similarity_max*Distance


        similarity_gap = similarity_gap.view(b, self.groups, h*w)
        similarity = similarity_max*self.zero+similarity_gap*self.one



        context = similarity - similarity.mean(dim=2, keepdim=True)
        std = context.std(dim=2, keepdim=True) + 1e-5
        context = (context/std).view(b,self.groups,h,w)
        # affine function
        context = context * self.weight + self.bias
        context = context.view(b*self.groups,1,h,w)\
            .expand(b*self.groups, c//self.groups, h, w).reshape(b,c,h,w)
        value = x*self.sig(context)

        return value

    def get_position_mask(self,x,b,h,w,number):
        mask = (x[0, 0, :, :] != 2020).nonzero()
        mask = (mask.reshape(h,w, 2)).permute(2,0,1).expand(b*number,2,h,w)
        return mask


    def get_query_position(self, query,groups):
        b,c,h,w = query.size()
        value = query.view(b*groups,c//groups,h,w)
        sumvalue = value.sum(dim=1,keepdim=True)
        maxvalue,maxposition = self.max_pool(sumvalue)
        t_position = torch.cat((maxposition//w,maxposition % w),dim=1)

        t_value = value[torch.arange(b*groups),:,t_position[:,0,0,0],t_position[:,1,0,0]]
        t_value = t_value.view(b, c, 1, 1)
        return t_value, t_position

    def get_similarity(self,query, key_value, mode='dotproduct'):
        if mode == 'dotproduct':
            similarity = torch.matmul(key_value.permute(0, 2, 1), query).squeeze(dim=1)
        elif mode == 'l1norm':
            similarity = -(abs(query - key_value)).sum(dim=1)
        elif mode == 'cosine':
            similarity = torch.cosine_similarity(query,key_value,dim=1)
        else:
            similarity = torch.matmul(key_value.permute(0, 2, 1), query)
        return similarity









def conv3x3(in_planes, out_planes, stride=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=1, bias=False)


def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride
        self.prm  = PRMLayer()

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.prm(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.conv1 = conv1x1(inplanes, planes)
        self.bn1 = nn.BatchNorm2d(planes)
        self.conv2 = conv3x3(planes, planes, stride)
        self.bn2 = nn.BatchNorm2d(planes)
        self.conv3 = conv1x1(planes, planes * self.expansion)
        self.bn3 = nn.BatchNorm2d(planes * self.expansion)
        self.prm  = PRMLayer()
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)
        out = self.prm(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self, block, layers, num_classes=1000, zero_init_residual=False):
        super(ResNet, self).__init__()
        self.inplanes = 64
        self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3,
                               bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, 64, layers[0])
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.fc = nn.Linear(512 * block.expansion, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

        # Zero-initialize the last BN in each residual branch,
        # so that the residual branch starts with zeros, and each residual block behaves like an identity.
        # This improves the model by 0.2~0.3% according to https://arxiv.org/abs/1706.02677
        if zero_init_residual:
            for m in self.modules():
                if isinstance(m, Bottleneck):
                    nn.init.constant_(m.bn3.weight, 0)
                elif isinstance(m, BasicBlock):
                    nn.init.constant_(m.bn2.weight, 0)

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)

        return x



def prm_resnet18(pretrained=False, **kwargs):
    """Constructs a ResNet-18 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(BasicBlock, [2, 2, 2, 2], **kwargs)
    return model


def prm_resnet34(pretrained=False, **kwargs):
    """Constructs a ResNet-34 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(BasicBlock, [3, 4, 6, 3], **kwargs)
    return model


def prm_resnet50(pretrained=False, **kwargs):
    """Constructs a ResNet-50 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(Bottleneck, [3, 4, 6, 3], **kwargs)
    return model


def prm_resnet101(pretrained=False, **kwargs):
    """Constructs a ResNet-101 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(Bottleneck, [3, 4, 23, 3], **kwargs)
    return model


def prm_resnet152(pretrained=False, **kwargs):
    """Constructs a ResNet-152 model.
    Args:
        pretrained (bool): If True, returns a model pre-trained on ImageNet
    """
    model = ResNet(Bottleneck, [3, 8, 36, 3], **kwargs)
    return model






def demo():
    st = time.perf_counter()
    for i in range(1):
        net = prm_resnet50(num_classes=1000)
        y = net(torch.randn(2, 3, 224,224))
        print(i)
    print("CPU time: {}".format(time.perf_counter() - st))

def demo2():
    st = time.perf_counter()
    for i in range(1):
        net = prm_resnet50(num_classes=1000).cuda()
        y = net(torch.randn(2, 3, 224,224).cuda())
        print(i)
        # print("Allocated: {}".format(torch.cuda.memory_allocated()))
    print("GPU time: {}".format(time.perf_counter() - st))

demo()
# demo2()
